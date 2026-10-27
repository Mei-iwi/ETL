"""Offline deployment contract; no image build or model inference."""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


class UniqueMappingLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        keys = [key.value for key, _ in node.value if key.tag != 'tag:yaml.org,2002:merge']
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate Compose mapping key')
        return super().construct_mapping(node, deep=deep)


def test_compose_files_have_no_duplicate_mapping_keys():
    for name in ('app', 'media', 'drive', 'auth', 'source-neon'):
        yaml.load((ROOT / f'deploy/compose.{name}.yml').read_text(), Loader=UniqueMappingLoader)


@pytest.mark.parametrize('service', ['api', 'source-file-worker', 'ocr-worker'])
def test_media_runtime_is_explicit_offline_and_read_only(service):
    overlay = yaml.safe_load((ROOT / 'deploy/compose.media.yml').read_text())
    spec = overlay['services'][service]
    assert spec['build']['args']['INSTALL_MEDIA'] == 'true'
    assert spec['environment']['SOURCE_METADATA_MEDIA_ENABLED'] == 'false'
    assert spec['environment']['WHISPER_MODEL_PATH'] == '/opt/models/whisper'
    mount, = spec['volumes']
    assert mount['read_only'] and not mount['bind']['create_host_path']
    assert ':?' in mount['source']


@pytest.mark.parametrize('script', ['etl-docker.ps1', 'etl-docker.sh'])
def test_wrappers_select_optional_media_overlay_from_env(script):
    text = (ROOT / 'deploy/scripts' / script).read_text()
    assert 'MEDIA_ENABLED' in text and 'compose.media.yml' in text
    assert 'Invalid MEDIA_ENABLED' in text
