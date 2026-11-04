"""Exercise the actual entrypoint origin parser before nginx is launched."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which('bash')
if os.name == 'nt':
    BASH = 'C:/Program Files/Git/bin/bash.exe'
pytestmark = pytest.mark.skipif(not BASH or not Path(BASH).exists(), reason='Bash required')


def policy(value):
    script = (ROOT / 'deploy/docker/auth-entrypoint.sh').read_text(encoding='utf-8')
    prefix = script.split('export ETL_FRAME_SOURCES', 1)[0]
    env = os.environ.copy()
    env.pop('ETL_VIEWER_FRAME_ORIGINS', None)
    if value is not None:
        env['ETL_VIEWER_FRAME_ORIGINS'] = value
    return subprocess.run([BASH, '-c', prefix + '\nprintf "%s" "$ETL_FRAME_SOURCES"'],
                          env=env, capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize('value', [None, '', 'https://files.example.test',
    'https://files.example.test https://bucket.example.test:443'])
def test_exact_https_viewers_are_allowed(value):
    result = policy(value)
    assert result.returncode == 0
    assert 'https://drive.google.com' in result.stdout
    if value:
        assert value in result.stdout


@pytest.mark.parametrize('value', ['*', 'https:', 'http://files.example.test',
    "https://files.example.test; script-src 'unsafe-inline'", 'https://*.example.test',
    'https://user:password@files.example.test', 'https://files.example.test/file.pdf',
    'https://files.example.test?signature=secret', ' '.join(['https://files.example.test'] * 17)])
def test_unsafe_viewer_sources_are_rejected(value):
    result = policy(value)
    assert result.returncode != 0
    assert result.stdout == ''
    assert 'VIEWER_ORIGINS_INVALID' in result.stderr
