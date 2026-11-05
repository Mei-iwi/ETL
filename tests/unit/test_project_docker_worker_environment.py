"""Test generated service isolation without reading real deployment credentials."""
import ast
from pathlib import Path

import pytest


def environment_builder():
    # The preparation script performs private file I/O at module scope. Execute
    # only its pure builder so tests never read or regenerate operator secrets.
    path = Path(__file__).resolve().parents[2] / 'deploy/scripts/prepare-project-docker.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == 'service_environment')
    scope = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), scope)
    return scope['service_environment']


@pytest.mark.parametrize('service', [
    'master-sync-worker', 'source-file-worker', 'ocr-worker',
    'reconciliation-worker', 'preflight',
])
def test_processing_services_start_without_graph_credentials(service):
    from data_sync_etl.config import Settings

    env = environment_builder()(service, {
        'NEO4J_ENABLED': 'true', 'NEO4J_SYNC_ENABLED': 'true',
        'NEO4J_SEARCH_ENABLED': 'true', 'NEO4J_RELATED_MODE': 'active',
        'AUTH_MODE': 'master_users', 'ADMIN_ENABLED': 'true',
        'OCR_COMPONENT_PIPELINE_ENABLED': 'true', 'INDEX_AUTO_ENABLED': 'true',
    })
    # Reproduce the Settings validation that previously crashed before the
    # worker's first heartbeat, with no graph password mounted in that service.
    settings = Settings(_env_file=None, **{
        k.lower(): v for k, v in env.items() if k.startswith('NEO4J_')
    })
    assert not settings.neo4j_enabled
    assert not settings.neo4j_sync_enabled
    assert not settings.neo4j_search_enabled
    assert settings.neo4j_related_mode == 'disabled'
    assert 'AUTH_MODE' not in env and 'ADMIN_ENABLED' not in env
    assert env['OCR_COMPONENT_PIPELINE_ENABLED'] == '${OCR_COMPONENT_PIPELINE_ENABLED}'
    assert env['INDEX_AUTO_ENABLED'] == '${INDEX_AUTO_ENABLED}'


def test_api_retains_graph_flags_and_existing_writeback_boundary():
    build = environment_builder()
    approved = {'NEO4J_ENABLED': 'true', 'NEO4J_RELATED_MODE': 'active',
                'SOURCE_VERSION_WRITEBACK_ENABLED': 'true'}
    api = build('api', approved)
    assert api['NEO4J_ENABLED'] == '${NEO4J_ENABLED}'
    assert api['NEO4J_RELATED_MODE'] == '${NEO4J_RELATED_MODE}'
    assert api['SOURCE_VERSION_WRITEBACK_ENABLED'] == '${SOURCE_VERSION_WRITEBACK_ENABLED}'
    assert build('ocr-worker', approved)['SOURCE_VERSION_WRITEBACK_ENABLED'] == 'false'
    assert build('reconciliation-worker', approved)['SOURCE_VERSION_WRITEBACK_ENABLED'] == '${SOURCE_VERSION_WRITEBACK_ENABLED}'
