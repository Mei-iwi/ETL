"""Generate temporary handoff artifacts outside the documentation tree."""
import ast
import csv
import inspect
import json
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))


def main():
    from data_sync_etl.api import routes
    from data_sync_etl.api.admin_access import protected
    from data_sync_etl.api.auth_routes import router as auth_router
    output = root / '.test-tmp/docker-portable/auth-handoff'
    output.mkdir(parents=True, exist_ok=True)
    evidence = output / 'evidence'
    evidence.mkdir(exist_ok=True)
    scratch = root / '.test-tmp/docker-portable'
    functions = {node.name: node for node in ast.parse(inspect.getsource(routes)).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def management(name, seen=None):
        seen = set() if seen is None else set(seen)
        if name in ('services', 'require_local_admin'):
            return True
        if name in seen or name not in functions:
            return False
        seen.add(name)
        calls = [node.func.id for node in ast.walk(functions[name]) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
        return any(management(call, seen) for call in calls)

    public = {'/health', '/api/v1/auth/config', '/api/v1/auth/csrf', '/api/v1/auth/login', '/api/v1/auth/logout'}
    with (output / 'endpoint-matrix.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['method', 'path', 'handler', 'openapi', 'auth_mode_principal', 'admin_feature_gate', 'csrf', 'data_policy'])
        for route in routes.router.routes + auth_router.routes:
            for method in sorted(route.methods):
                path = route.path
                unsafe = method not in ('GET', 'HEAD', 'OPTIONS')
                writer.writerow([method, path, route.endpoint.__name__, route.include_in_schema,
                    'public-with-gateway' if path in public and path != '/health' else 'public-liveness' if path == '/health' else 'server-ADMIN-required',
                    'YES' if not path.startswith('/api/v1/auth/') and (protected(path, method) or management(route.endpoint.__name__)) else 'NO',
                    'YES' if unsafe else 'NO', 'existing ownership/eligibility/freshness checks remain'])
        writer.writerow(['GET', '/openapi.json', 'FastAPI schema', False, 'server-ADMIN-required', 'NO', 'NO', 'no credential/session values'])
    statuses = {
        'T01': ('PASS', 'TLS fixture Secure/__Host cookie; no production CA claim'),
        'T02': ('PASS', 'generic wrong email/password unit and browser'),
        'T03': ('PASS', 'persistent bounded account/source throttle'),
        'T04': ('PASS', 'idle/absolute unit; browser401 clears UI'),
        'T05': ('PASS', 'revoke/clear cookie and browser logout'),
        'T06': ('PASS', 'CSRF/Origin unit; real HTTP and HTTPS403'),
        'T07': ('PASS', 'forged/rotated/revoked cookie and epoch regression'),
        'T08': ('PASS', 'remember idle/absolute and persistent browser cookie'),
        'T09': ('PASS', 'ADMIN_ENABLED true still401 before login'),
        'T10': ('PASS', 'authenticated feature false403'),
        'T11': ('PASS', 'unknown/private/archived source checks and native regression'),
        'T12': ('PASS', 'authenticated SOURCE metadata PATCH409'),
        'T13': ('PASS', 'all private routes/aliases reject anonymous before handler'),
        'T14': ('PASS', 'authenticated bow/vector/hybrid excludes inactive; existing freshness regression'),
        'T15': ('PASS', 'local_demo Docker browser server ADMIN; no login'),
        'T16': ('PASS', 'invalid production bypass fails mode validation'),
        'T17': ('PASS', 'effective real Compose with0.0.0.0 rejected before startup; supported launcher boundary'),
        'T18': ('PASS', 'forged forwarded/Host/Origin/gateway key rejected'),
        'T19': ('PARTIAL', 'real same-origin HTTPS fixture PASS; organizational cert/LAN second host NOT_RUN'),
        'T20': ('PASS', 'synthetic auth secret absence scan image files/config/history/logs and CLI test'),
        'T21': ('PASS', 'real API Docker restart preserves cookie-backed session'),
        'T22': ('PARTIAL', 'actual Blade/CSS port and Edge keyboard PASS; independent fidelity approval NOT_RUN'),
        'T23': ('PASS', 'light/dark/320px/reduced-motion; screenshots visually inspected'),
        'T24': ('PASS', 'deep-link, expiry and external returnTo rejection'),
        'T25': ('PASS', '331 native regression +9 real database/model/OCR pipeline'),
        'T26': ('PARTIAL', 'BoW/Vector/Hybrid authenticated scope and algorithm regression PASS; historical local_demo evaluation read mapping requires approval'),
    }
    with (output / 'acceptance-matrix.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['ID', 'status', 'evidence_scope'])
        writer.writerows((key, *value) for key, value in statuses.items())
    for filename in ['login-desktop-light.png', 'login-desktop-dark.png', 'login-mobile-dark.png',
                     'login-compact-viewport.png', 'login-error.png', 'local-demo-no-login.png',
                     'evidence.json', 'local-demo-evidence.json']:
        path = scratch / 'auth-browser' / filename
        if path.is_file():
            shutil.copyfile(path, evidence / filename)
    for filename in ['auth-artifact-evidence.json', 'auth-profile-evidence.json']:
        shutil.copyfile(scratch / filename, evidence / filename)
    # Keep the decisive output and historical broad-run failures in reviewable logs.
    for filename in ['auth-focused.log', 'auth-final-docker-unit.log', 'auth-frontend-tests.log',
                     'auth-regression.log', 'auth-regression-fixed.log', 'auth-portable-integration.log',
                     'auth-e2e.log', 'auth-bypass-e2e.log', 'auth-tls-e2e.log', 'auth-artifact-check.log', 'auth-profile-check.log']:
        raw = (scratch / filename).read_bytes()
        content = raw.decode('utf-16' if raw.startswith(b'\xff\xfe') else 'utf-8', errors='replace')
        (evidence / filename).write_text(content, encoding='utf-8')
    for filename in ['native-unit-acceptance.log', 'source-ocr-index-search-real.log', 'RESULTS.json']:
        path = scratch / 'etl-portable-test-c6d0e11bc0' / filename
        raw = path.read_bytes()
        (evidence / filename).write_text(raw.decode('utf-16' if raw.startswith(b'\xff\xfe') else 'utf-8-sig', errors='replace'), encoding='utf-8')
    (output / 'changed-files.txt').write_text(subprocess.check_output(['git', 'diff', '--name-only'], cwd=root, text=True)
        + subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'], cwd=root, text=True), encoding='utf-8')
    status = subprocess.check_output(['git', 'status', '--short', '--untracked-files=all'], cwd=root, text=True)
    stashes = subprocess.check_output(['git', 'stash', 'list', '--format=%gd %H'], cwd=root, text=True)
    (output / 'git-status.txt').write_text(status + '\nExisting stashes (read only):\n' + stashes, encoding='utf-8')
    (evidence / 'snapshot.json').write_text(json.dumps({'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        'branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=root, text=True).strip(),
        'note': 'No commit/push/merge; files include dirty Docker-portable baseline. Do not sum overlapping suites.'}, indent=2))
    print('PASS: endpoint/Git inventories and synthetic evidence written')


if __name__ == '__main__':
    main()
