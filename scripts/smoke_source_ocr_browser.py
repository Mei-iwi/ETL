"""Drive the existing Chrome test profile through real SOURCE upload and UI.

Requires an already running local API, frontend, CLI workers and an isolated
Chrome debugging endpoint. Reads credentials from a private local file only.
"""
import argparse
import atexit
import base64
import json
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import text
from websockets.sync.client import connect

from data_sync_etl.config import Settings
from data_sync_etl.db.session import make_engine


class Browser:
    def __init__(self, endpoint, evidence):
        target = httpx.put(endpoint + '/json/new?about:blank').json()
        self.socket = connect(target['webSocketDebuggerUrl'], max_size=20_000_000, legacy=True)
        self.sequence, self.network = 0, []
        self.requests = []
        self.evidence = evidence
        atexit.register(self.save_network)
        self.call('Page.enable')
        self.call('Network.enable')
        self.call('Emulation.setDeviceMetricsOverride', dict(width=1440, height=1100, deviceScaleFactor=1, mobile=False))

    def save_network(self):
        self.evidence.with_name('browser-upload-network.json').write_text(json.dumps(self.network, indent=2), encoding='utf-8')

    def call(self, method, params=None):
        self.sequence += 1
        request_id = self.sequence
        self.socket.send(json.dumps(dict(id=request_id, method=method, params=params or {})))
        while True:
            message = json.loads(self.socket.recv(timeout=60))
            if message.get('method') == 'Network.requestWillBeSent':
                request = message['params']['request']
                if '/api/' in request['url']:
                    self.requests.append({'path': request['url'].split('/api/', 1)[1], 'method': request['method']})
            if message.get('method') == 'Network.responseReceived':
                response = message['params']['response']
                if '/api/' in response['url']:
                    self.network.append(dict(path=response['url'].split('/api/', 1)[1], status=response['status']))
            if message.get('id') == request_id:
                if 'error' in message:
                    raise RuntimeError('CDP_REQUEST_FAILED')
                return message.get('result', {})

    def js(self, expression):
        result = self.call('Runtime.evaluate', dict(expression=expression, returnByValue=True, awaitPromise=True))
        if 'exceptionDetails' in result:
            raise RuntimeError('BROWSER_EXPRESSION_FAILED')
        return result.get('result', {}).get('value')

    def wait(self, expression, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.js(expression):
                return True
            time.sleep(1)
        return False

    def navigate(self, url):
        self.call('Page.navigate', {'url': url})

    def screenshot(self, name):
        data = self.call('Page.captureScreenshot', {'format': 'png'})['data']
        self.evidence.with_name(name).write_bytes(base64.b64decode(data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials-file', type=Path, required=True)
    parser.add_argument('--pdf', type=Path, required=True)
    parser.add_argument('--cdp-url', default='http://127.0.0.1:9225')
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--resume-version', help='Resume the already uploaded, owned fixture after a browser restart')
    parser.add_argument('--retry-finalization', action='store_true', help='Click the real retry control for the resumed failed fixture')
    args = parser.parse_args()
    settings = Settings()
    assert settings.app_environment in ('development', 'test'), 'DEV_ONLY'
    assert httpx.URL(settings.auth_origin).host in ('localhost', '127.0.0.1'), 'LOCAL_UI_REQUIRED'
    policy = json.loads(settings.source_ocr_contract_file.read_text(encoding='utf-8'))
    assert policy.get('allow_etl_created_versions') is True, 'UPLOAD_RECEIPT_CONTRACT_REQUIRED'
    credentials = json.loads(args.credentials_file.read_text(encoding='utf-8'))
    browser, evidence = Browser(args.cdp_url, args.evidence), {}

    def record(key, value):
        evidence[key] = value
        args.evidence.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        print(key, value, flush=True)

    browser.call('Network.clearBrowserCookies')
    browser.navigate(settings.auth_origin + '/login')
    assert browser.wait("!!document.querySelector('#login-email')"), 'LOGIN_FORM_MISSING'
    browser.js("(()=>{const credentials=" + json.dumps(credentials) + ";"
        "const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
        "for(const [id,value] of [['login-email',credentials.email],['login-password',credentials.password]]){"
        "const input=document.getElementById(id);set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));}"
        "document.querySelector('button[type=submit]').click();return true})()")
    assert browser.wait("!location.pathname.startsWith('/login')"), 'BROWSER_LOGIN_FAILED'
    record('browser_source_admin_login', True)
    if args.resume_version:
        engine = make_engine(settings.database_url)
        with engine.connect() as conn:
            owned = conn.execute(text(
                "SELECT r.id,j.id AS job_id FROM resource_versions v JOIN master_learning_resources r ON r.id=v.resource_id "
                "JOIN ocr_jobs j ON j.resource_version_id=v.id WHERE v.id=:id AND r.title=:title "
                "AND r.publication_status='DRAFT'"
            ), {'id': args.resume_version, 'title': 'CODEX E2E SOURCE OCR browser 20261103'}).mappings().one()
        engine.dispose()
        browser.navigate(settings.auth_origin + '/library/processing?resourceId=' + owned['id']
                         + '&versionId=' + args.resume_version + '&jobId=' + owned['job_id'])
    else:
        upload(browser, args, settings, record)
    assert browser.wait("location.pathname==='/library/processing'", timeout=300), 'SOURCE_UPLOAD_OR_PROCESS_FAILED'
    version_id = browser.js("new URLSearchParams(location.search).get('versionId')")
    if args.retry_finalization:
        assert args.resume_version, 'RETRY_REQUIRES_OWNED_RESUMED_FIXTURE'
        assert browser.wait("[...document.querySelectorAll('button')].some(b=>b.textContent==='Xử lý lại' && !b.disabled)", timeout=120), 'RETRY_CONTROL_MISSING'
        browser.screenshot('browser-finalization-failed.png')
        browser.js("[...document.querySelectorAll('button')].find(b=>b.textContent==='Xử lý lại').click();true")
        record('browser_retry_submitted', True)
    finish(browser, args, settings, policy, version_id, record)


def upload(browser, args, settings, record):
    browser.navigate(settings.auth_origin + '/library/add')
    assert browser.wait("document.querySelector('#providerId')?.options.length>1 && document.querySelector('#resourceType')?.options.length>1"), 'UPLOAD_CATALOG_MISSING'
    browser.js("(()=>{document.getElementById('title').value='CODEX E2E SOURCE OCR browser 20261103';"
        "for(const id of ['resourceType','providerId']){const input=document.getElementById(id);"
        "const choice=id==='resourceType'?[...input.options].find(o=>o.textContent.includes('Tài liệu đọc')):input.options[1];"
        "Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(input,choice.value);"
        "input.dispatchEvent(new Event('change',{bubbles:true}));}"
        "document.getElementById('publicationStatus').value='DRAFT';return true})()")
    document = browser.call('DOM.getDocument')['root']['nodeId']
    file_node = browser.call('DOM.querySelector', {'nodeId': document, 'selector': '#file'})['nodeId']
    browser.call('DOM.setFileInputFiles', {'nodeId': file_node, 'files': [str(args.pdf.resolve())]})
    assert browser.wait("!document.querySelector('button[type=submit]').disabled"), 'UPLOAD_FORM_DISABLED'
    browser.screenshot('browser-upload-form.png')
    browser.js("document.querySelector('button[type=submit]').click();true")
    record('browser_upload_submitted', True)


def finish(browser, args, settings, policy, version_id, record):
    record('browser_master_version', version_id)
    assert browser.wait("document.body.innerText.includes('Xử lý hoàn thành')", timeout=900), 'BROWSER_PROCESS_NOT_READY'
    record('browser_processing_ready', True)
    browser.js("document.querySelector('button[aria-controls=\"original-ocr-content\"]').click();true")
    assert browser.wait("document.querySelector('#original-ocr-content pre')?.textContent.toLowerCase().includes('photosynthesis')", timeout=120), 'RAW_OCR_UI_MISSING'
    browser.js("[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Xem nội dung trích xuất'))?.click();true")
    assert browser.wait("[...document.querySelectorAll('article p')].some(p=>p.textContent.toLowerCase().includes('photosynthesis'))", timeout=90), 'NORMALIZED_UNITS_UI_MISSING'
    browser.js("document.querySelector('#original-ocr-content').scrollIntoView();true")
    browser.screenshot('browser-upload-ocr-units.png')
    record('browser_raw_and_units', True)
    engine = make_engine(settings.database_url)
    with engine.connect() as conn:
        fixture = dict(conn.execute(text(
            'SELECT v.id AS master_version_id,r.id AS master_resource_id,r.source_record_key AS source_resource_id, '
            'f.source_version_id FROM resource_versions v JOIN master_learning_resources r ON r.id=v.resource_id '
            'JOIN source_file_imports f ON f.target_version_id=v.id WHERE v.id=:id'
        ), {'id': version_id}).mappings().one())
    engine.dispose()
    fixture['fixture_name'] = 'browser'
    record('fixture', fixture)
    assert fixture['source_version_id'] not in policy['allowed_version_ids'], 'NEW_VERSION_MUST_USE_RECEIPT_POLICY'
    record('new_upload_without_version_allowlist_edit', True)
    status_expression = "fetch('/api/v1/resource-versions/" + version_id + "/index-status').then(r=>r.json())"
    assert browser.wait("(" + status_expression + ").then(s=>s.index_status==='READY')", timeout=600), 'INITIAL_INDEX_NOT_READY'
    browser.navigate(settings.auth_origin + '/search/query-methods?resource_id=' + fixture['master_resource_id']
                     + '&resource_version_id=' + version_id)
    assert browser.wait("!!document.querySelector('#search-query')"), 'SEARCH_FORM_MISSING'
    browser.js("(()=>{const input=document.getElementById('search-query');"
        "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'photosynthesis');"
        "input.dispatchEvent(new Event('input',{bubbles:true}));return true})()")
    assert browser.wait("!document.querySelector('form[role=search] button[type=submit]').disabled"), 'MODEL_NOT_READY'
    browser.js("document.querySelector('form[role=search] button[type=submit]').click();true")
    assert browser.wait("document.body.innerText.includes('CODEX E2E SOURCE OCR browser 20261103')", timeout=120), 'BROWSER_SEARCH_MISSING'
    assert browser.wait("!!document.querySelector('a[href*=\"" + fixture['master_resource_id']
                        + "\"][href*=\"" + version_id + "\"]')"), 'EXACT_FIXTURE_SEARCH_LINK_MISSING'
    browser.screenshot('browser-upload-search.png')
    record('browser_uploaded_fixture_search', True)
    browser.navigate(settings.auth_origin + '/library/indexing?resourceId=' + fixture['master_resource_id']
                     + '&versionId=' + version_id)
    assert browser.wait("[...document.querySelectorAll('button')].some(b=>b.textContent==='Tái lập chỉ mục' && !b.disabled)", timeout=180), 'REINDEX_UI_UNAVAILABLE'
    before = browser.js(status_expression)
    browser.js("[...document.querySelectorAll('button')].find(b=>b.textContent==='Tái lập chỉ mục').click();true")
    assert browser.wait("!!document.querySelector('dialog[open]')"), 'REINDEX_CONFIRMATION_MISSING'
    browser.js("[...document.querySelectorAll('dialog button')].find(b=>b.textContent==='Xác nhận').click();true")
    assert browser.wait("(" + status_expression + ").then(s=>s.index_status==='READY' && s.published_run_id!=="
                        + json.dumps(before.get('published_run_id')) + ")", timeout=600), 'REINDEX_NOT_READY'
    browser.js("[...document.querySelectorAll('button')].find(b=>b.textContent==='Tải lại trạng thái')?.click();true")
    assert browser.wait("document.body.innerText.includes('Chỉ mục sẵn sàng.')"), 'REINDEX_UI_STATUS_MISSING'
    browser.screenshot('browser-reindex-ready.png')
    record('browser_reindex_new_published_run_ready', True)
    browser.save_network()
    browser.socket.close()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        main()
    except Exception as error:
        message = str(error)
        print('BROWSER_FAILED', type(error).__name__, message if message.isupper() and len(message) < 100 else 'inspect local evidence')
        raise SystemExit(1) from None
