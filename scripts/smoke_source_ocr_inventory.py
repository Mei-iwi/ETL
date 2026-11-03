"""Create an authorized DRAFT through the existing browser UI, without /process.

Navigate away after the durable upload response so only the normal worker can
start OCR. Does not read cookies, tokens, credentials or browser storage.
"""
import argparse
import json
import time
from pathlib import Path

from smoke_source_ocr_browser import Browser

from data_sync_etl.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.local/codex_reports/ocr_component_master_sync'))
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--waiting-version')
    args = parser.parse_args()
    settings = Settings()
    assert settings.app_environment in ('development', 'test')
    assert settings.auth_origin == 'http://127.0.0.1:3000'
    evidence = args.root / 'inventory-ui-create-evidence.json'
    browser = Browser('http://127.0.0.1:9225', evidence)
    if args.waiting_version:
        browser.navigate(settings.auth_origin + '/library/processing?versionId=' + args.waiting_version)
        assert browser.wait("document.body.innerText.includes('Đang chờ quyền xử lý tài liệu.')", timeout=90), 'WAITING_NOTICE_MISSING'
        summary = browser.js("fetch('/api/v1/system/source-ocr-inventory').then(async r=>({status:r.status,body:await r.json()}))")
        assert summary['status'] == 200 and summary['body']['classified'] > 0
        browser.screenshot('inventory-waiting-auth-ui.png')
        (args.root / 'inventory-api-evidence.json').write_text(json.dumps({
            'summary_http': summary, 'waiting_status_visible': True, 'waiting_version': args.waiting_version,
        }, indent=2), encoding='utf-8')
        browser.socket.close()
        print('inventory_summary_http_and_waiting_ui_pass', flush=True)
        return
    if args.verify:
        verify(browser, settings, args.root)
        return
    browser.navigate(settings.auth_origin + '/library/add')
    assert browser.wait("document.querySelector('#providerId')?.options.length>1"), 'AUTHORIZED_UPLOAD_UI_REQUIRED'
    browser.js("(()=>{document.getElementById('title').value='CODEX E2E SOURCE OCR inventory-auto 20261103';"
        "for(const id of ['resourceType','providerId']){const input=document.getElementById(id);"
        "const option=id==='resourceType'?[...input.options].find(o=>o.textContent.includes('Tài liệu đọc')):input.options[1];"
        "Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(input,option.value);"
        "input.dispatchEvent(new Event('change',{bubbles:true}));}"
        "document.getElementById('publicationStatus').value='DRAFT';return true})()")
    root = browser.call('DOM.getDocument')['root']['nodeId']
    node = browser.call('DOM.querySelector', {'nodeId': root, 'selector': '#file'})['nodeId']
    browser.call('DOM.setFileInputFiles', {'nodeId': node, 'files': [str((args.root / 'fixture-scanned.pdf').resolve())]})
    browser.js("document.querySelector('button[type=submit]').click();true")
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        browser.js('true')
        if any(item['path'] == 'v1/resources' and item['status'] == 202 for item in browser.network):
            # The request is now durable; stop this UI before its explicit
            # startProcessing action. Metadata/writeback and OCR workers own continuation.
            browser.navigate('about:blank')
            break
        time.sleep(.1)
    else:
        raise AssertionError('DURABLE_SOURCE_CREATE_NOT_ACCEPTED')
    assert not any(item['path'].endswith('/process') for item in browser.requests), 'MANUAL_PROCESS_REQUEST_SENT'
    evidence.write_text(json.dumps({'ui_upload_accepted': True, 'manual_process_requests': 0,
                                   'requests': browser.requests, 'responses': browser.network}, indent=2), encoding='utf-8')
    browser.socket.close()
    print('ui_metadata_upload_accepted_no_manual_process', True, flush=True)


def verify(browser, settings, root):
    fixture = json.loads((root / 'inventory-auto-fixture.json').read_text())
    version = fixture['master_version_id']
    browser.navigate(settings.auth_origin + '/library/processing?versionId=' + version)
    assert browser.wait("document.body.innerText.includes('Xử lý hoàn thành')", timeout=900), 'AUTOMATIC_OCR_NOT_READY'
    status = "fetch('/api/v1/resource-versions/" + version + "/index-status?include_bags=false').then(r=>r.json())"
    assert browser.wait('(' + status + ").then(s=>s.index_status==='READY')", timeout=600), 'AUTOMATIC_INDEX_NOT_READY'
    ready = browser.js(status)
    assert ready['embedding_dimension'] == 1024 and ready['bag_count'] >= 1
    audit = browser.js("fetch('/api/v1/resource-versions/" + version + "/etl-status').then(r=>r.json()).then(s=>s.ocr_audit)")
    browser.screenshot('inventory-auto-ready.png')
    resource = fixture['master_resource_id']
    methods = []
    for method in ('bow', 'vector', 'hybrid'):
        browser.navigate(settings.auth_origin + '/search/query-methods?q=photosynthesis&method=' + method
                         + '&resource_id=' + resource + '&resource_version_id=' + version)
        assert browser.wait("!!document.querySelector('a[href*=\"" + resource + "\"][href*=\"" + version + "\"]')",
                            timeout=120), 'AUTOMATIC_SEARCH_FIXTURE_MISSING'
        methods.append(method)
    browser.screenshot('inventory-auto-search.png')
    assert not any(item['path'].endswith('/process') for item in browser.requests), 'MANUAL_PROCESS_REQUEST_SENT'
    (root / 'inventory-auto-evidence.json').write_text(json.dumps({
        'automatic_processing_ready': True, 'automatic_index_ready': True, 'embedding_dimension': 1024,
        'bag_count': ready['bag_count'], 'audit_http': audit, 'search_methods': methods,
        'manual_process_requests': 0, 'network': browser.network,
    }, indent=2), encoding='utf-8')
    browser.socket.close()
    print('automatic_metadata_ocr_index_search_ui_pass', methods, flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('INVENTORY_UI_FAILED', type(error).__name__)
        raise SystemExit(1) from None
