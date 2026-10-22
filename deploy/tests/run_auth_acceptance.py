"""Host QA runner: synthetic secrets; no passwords in argv, files or evidence."""
import argparse
import os
import secrets
import ssl
import subprocess
import time
from pathlib import Path
from urllib.request import HTTPSHandler, ProxyHandler, build_opener

from argon2 import PasswordHasher


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['single_admin', 'local_demo'], default='single_admin')
    parser.add_argument('--tls', action='store_true')
    args = parser.parse_args()
    if args.tls and args.mode != 'single_admin':
        parser.error('TLS is incompatible with local bypass')
    root = Path(__file__).resolve().parents[2]
    output = root / '.test-tmp/docker-portable/auth-acceptance'
    private = output / 'private'
    private.mkdir(parents=True, exist_ok=True)
    password = secrets.token_urlsafe(24)
    for name, value in {'admin_password_hash': PasswordHasher().hash(password),
                        'session_signing_key': secrets.token_hex(32), 'auth_proxy_key': secrets.token_hex(32)}.items():
        path = private / name
        path.write_text(value, encoding='ascii')
        path.chmod(0o640 if name == 'auth_proxy_key' else 0o600)
    env = {**os.environ, 'AUTH_TEST_SECRET_DIR': private.as_posix(), 'ETL_AUTH_TEST_PASSWORD': password,
           'ETL_AUTH_TEST_MODE': args.mode, 'AUTH_MODE': args.mode,
           'AUTH_DEV_ADMIN_BYPASS': 'true' if args.mode == 'local_demo' else 'false',
           'AUTH_APP_ENVIRONMENT': 'local' if args.mode == 'local_demo' else 'production',
           'ETL_AUTH_TEST_DOCKER_CONFIG': str(root / '.test-tmp/docker-portable/docker-client')}
    docker = ['docker', '--config', str(root / '.test-tmp/docker-portable/docker-client')]
    compose = docker + ['compose', '-f', str(root / 'deploy/tests/compose.auth-test.yml')]
    origin = 'http://127.0.0.1:18088'
    context = None
    if args.tls:
        from datetime import datetime, timedelta, timezone
        from ipaddress import ip_address

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        tls = output / 'tls'
        tls.mkdir(exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'ETL isolated test only')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
                .not_valid_after(now + timedelta(hours=1))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address('127.0.0.1')), x509.DNSName('localhost')]), critical=False)
                .sign(key, hashes.SHA256()))
        (tls / 'server.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (tls / 'server.key').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        (tls / 'server.key').chmod(0o600)
        context = ssl.create_default_context(cafile=str(tls / 'server.crt'))
        env.update(AUTH_TEST_TLS_DIR=tls.as_posix(), NODE_EXTRA_CA_CERTS=str(tls / 'server.crt'))
        origin = 'https://127.0.0.1:18443'
        compose += ['-f', str(root / 'deploy/tests/compose.auth-test-tls.yml')]
    env.update(AUTH_TEST_ORIGIN=origin, ETL_AUTH_TEST_ORIGIN=origin)
    subprocess.run(compose + ['up', '-d', '--no-build'], env=env, check=True, cwd=root)
    try:
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
        for _ in range(90):
            try:
                with opener.open(origin + '/health', timeout=2) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError('Auth fixture startup timeout; inspect fixture-only logs')
        subprocess.run(['node', '--test', 'deploy/tests/auth_browser.test.mjs'], env=env, check=True, cwd=root)
    finally:
        # Keep owned volumes for restart checks; never prune or remove data volumes.
        subprocess.run(compose + ['stop'], env=env, check=True, cwd=root)


if __name__ == '__main__':
    main()
