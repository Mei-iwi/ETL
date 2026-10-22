"""Inspect only synthetic auth secrets against built artifacts; never print values."""
import json
import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[2]
    output = root / '.test-tmp/docker-portable'
    docker = ['docker', '--config', str(output / 'docker-client')]
    private = output / 'auth-acceptance/private'
    needles = [p.read_text(encoding='ascii').strip() for p in private.iterdir() if p.is_file()]
    assert len(needles) == 3 and all(needles)
    backend, frontend = 'etl-portable-backend:auth-20261022', 'etl-portable-frontend:auth-20261022'
    backend_check = """
import json,sys
from pathlib import Path
values=[s.encode() for s in json.load(sys.stdin)]
for root in ['/app','/opt/venv/lib/python3.12/site-packages/data_sync_etl']:
    assert Path(root).is_dir()
    for path in Path(root).rglob('*'):
        if path.is_file() and path.suffix in ('.py','.json','.ini','.sql','.toml','.md'):
            assert not any(value in path.read_bytes() for value in values), 'AUTH_SECRET_IN_IMAGE'
print('PASS: backend secret absence')
"""
    subprocess.run(docker + ['run', '--rm', '-i', '--network', 'none', '--read-only', '--entrypoint', 'python', backend,
                             '-c', backend_check], input=json.dumps(needles), text=True, check=True)
    shell = 'test -d /usr/share/nginx/html || exit 2; while IFS= read -r needle; do if grep -r -F -q -- "$needle" /usr/share/nginx/html; then exit 1; fi; done'
    subprocess.run(docker + ['run', '--rm', '-i', '--network', 'none', '--read-only', '--entrypoint', 'sh', frontend,
                             '-c', shell], input='\n'.join(needles) + '\n', text=True, check=True)
    images = {}
    for name in (backend, frontend):
        metadata = subprocess.check_output(docker + ['image', 'inspect', name], text=True)
        history = subprocess.check_output(docker + ['history', '--no-trunc', '--format', '{{.CreatedBy}}', name], text=True)
        assert not any(value in metadata + history for value in needles), 'AUTH_SECRET_IN_IMAGE_METADATA'
        images[name] = json.loads(metadata)[0]['Id']
    for filename in ('auth-backend-build.log', 'auth-frontend-build.log', 'auth-e2e.log', 'auth-tls-e2e.log', 'auth-bypass-e2e.log'):
        raw = (output / filename).read_bytes()
        content = raw.decode('utf-16' if raw.startswith(b'\xff\xfe') else 'utf-8', errors='replace')
        assert not any(value in content for value in needles), 'AUTH_SECRET_IN_LOG'
    # Exercise the exact offline CLI inside the runtime image. Password remains in RAM.
    cli_check = """
import secrets,subprocess,sys
from pathlib import Path
from argon2 import PasswordHasher
root=Path('/run/etl/cli-acceptance');root.mkdir()
password=secrets.token_urlsafe(24)
cmd=[sys.executable,'-m','data_sync_etl.security.cli','init','--directory',str(root),'--password-stdin']
result=subprocess.run(cmd,input=password+'\\n',text=True,capture_output=True)
assert result.returncode==0 and password not in result.stdout+result.stderr
encoded=(root/'admin_password_hash').read_text().strip()
assert encoded.startswith('$argon2id$') and PasswordHasher().verify(encoded,password)
again=subprocess.run(cmd,input=password+'\\n',text=True,capture_output=True)
assert again.returncode==1 and (root/'admin_password_hash').read_text().strip()==encoded
assert (root/'session_signing_key').stat().st_mode & 0o777 == 0o600
assert (root/'auth_proxy_key').stat().st_mode & 0o777 == 0o640
print('PASS: runtime CLI hash, permissions, no plaintext and overwrite rejection')
"""
    subprocess.run(docker + ['run', '--rm', '--network', 'none', '--read-only', '--tmpfs', '/run/etl:rw,uid=10001,gid=10001,mode=0700',
                             '--entrypoint', 'python', backend, '-c', cli_check], check=True)
    (output / 'auth-artifact-evidence.json').write_text(json.dumps({'status': 'PASS', 'images': images,
        'scope': 'synthetic auth secret values; app/site-package/static asset contents, image config/history, selected build/runtime logs; offline CLI'}, indent=2))
    print('PASS: auth artifact scan and offline CLI')


if __name__ == '__main__':
    main()
