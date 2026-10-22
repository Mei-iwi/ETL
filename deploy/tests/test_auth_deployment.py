import copy
import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / 'runtime/auth_deployment.py'
spec = importlib.util.spec_from_file_location('auth_deployment', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    return {'services': {
        'api': {'read_only': True, 'environment': {'AUTH_MODE': 'single_admin', 'APP_ENVIRONMENT': 'production',
            'AUTH_ORIGIN': 'http://127.0.0.1:8080', 'AUTH_DEPLOYMENT_PROFILE': 'localhost',
            'AUTH_PUBLIC_ENABLED': 'false', 'AUTH_DEV_ADMIN_BYPASS': 'false'},
            'secrets': ['admin_password_hash', 'session_signing_key', 'auth_proxy_key']},
        'frontend': {'read_only': True, 'ports': [{'host_ip': '127.0.0.1', 'published': '8080', 'target': 8080}],
            'environment': {'AUTH_MODE': 'single_admin', 'AUTH_ORIGIN': 'http://127.0.0.1:8080', 'AUTH_TLS': 'false'},
            'secrets': ['auth_proxy_key']},
        'ocr-worker': {'secrets': ['connections']}}}


def test_approved_local_login_and_local_bypass():
    doc = fixture()
    module.validate(doc)
    env = doc['services']['api']['environment']
    env.update(AUTH_MODE='local_demo', APP_ENVIRONMENT='local', AUTH_DEV_ADMIN_BYPASS='true')
    doc['services']['frontend']['environment']['AUTH_MODE'] = 'local_demo'
    module.validate(doc)


@pytest.mark.parametrize('change,code', [
    (lambda d: d['services']['api'].update(ports=[{'published': '8000'}]), 'API_MUST_BE_PRIVATE'),
    (lambda d: d['services']['api'].update(network_mode='host'), 'API_MUST_BE_PRIVATE'),
    (lambda d: d['services']['frontend']['ports'][0].update(host_ip='0.0.0.0'), 'LOOPBACK_BIND_REQUIRED'),
    (lambda d: d['services']['frontend']['ports'][0].update(host_ip='::'), 'LOOPBACK_BIND_REQUIRED'),
    (lambda d: d['services']['frontend']['ports'][0].pop('host_ip'), 'LOOPBACK_BIND_REQUIRED'),
    (lambda d: d['services']['frontend']['ports'][0].update(published='8081'), 'ORIGIN_PORT_MISMATCH'),
    (lambda d: d['services']['api'].update(deploy={'replicas': 2}), 'SINGLE_INSTANCE_REQUIRED'),
    (lambda d: d['services']['ocr-worker'].update(secrets=['session_signing_key']), 'SECRET_SCOPE_INVALID'),
    (lambda d: d['services']['ocr-worker'].update(secrets=['auth_proxy_key']), 'SECRET_SCOPE_INVALID'),
    (lambda d: d['services']['frontend'].update(secrets=['admin_password_hash']), 'SECRET_SCOPE_INVALID'),
    (lambda d: d['services']['api']['environment'].update(AUTH_DEV_ADMIN_BYPASS='true'), 'BYPASS_FORBIDDEN'),
    (lambda d: d['services']['api']['environment'].update(AUTH_PUBLIC_ENABLED='true'), 'LOCAL_PROFILE_INVALID'),
    (lambda d: d['services']['api'].update(read_only=False), 'READ_ONLY_REQUIRED'),
])
def test_invalid_effective_config_blocks_start(change, code):
    doc = copy.deepcopy(fixture())
    change(doc)
    with pytest.raises(ValueError, match=code):
        module.validate(doc)


def test_lan_tls_and_bypass_rejected():
    doc = fixture()
    env, web = doc['services']['api']['environment'], doc['services']['frontend']['environment']
    env.update(AUTH_ORIGIN='https://etl.test:8443', AUTH_DEPLOYMENT_PROFILE='multi_machine',
               AUTH_PUBLIC_ENABLED='true', AUTH_COOKIE_SECURE='true')
    web.update(AUTH_ORIGIN=env['AUTH_ORIGIN'], AUTH_TLS='true')
    doc['services']['frontend']['ports'][0].update(host_ip='0.0.0.0', published='8443', target=8443)
    module.validate(doc)
    env.update(AUTH_MODE='local_demo', AUTH_DEV_ADMIN_BYPASS='true')
    web['AUTH_MODE'] = 'local_demo'
    with pytest.raises(ValueError, match='TLS_REQUIRED'):
        module.validate(doc)
