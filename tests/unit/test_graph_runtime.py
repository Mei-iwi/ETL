import sys

import pytest
from pydantic import ValidationError

from data_sync_etl.adapters.neo4j.driver import GraphDriver, GraphUnavailable
from data_sync_etl.config import Settings


def test_disabled_graph_never_imports_driver(monkeypatch):
    monkeypatch.setitem(sys.modules, 'neo4j', None)
    driver = GraphDriver(Settings(_env_file=None))
    with pytest.raises(GraphUnavailable, match='GRAPH_DISABLED'):
        driver.preflight()
    driver.close()
    assert driver._driver is None


@pytest.mark.parametrize('uri', [
    'bolt://user:secret@localhost:7687', 'https://localhost',
    'bolt://remote.example:7687', 'neo4j+ssc://remote.example',
    'bolt://localhost:7687/path', 'bolt://localhost:invalid',
])
def test_graph_rejects_unsafe_uri(uri):
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, neo4j_uri_host=uri)
    assert 'GRAPH_URI_INVALID' in str(exc.value)
    assert uri not in str(exc.value)


def test_graph_requires_private_credentials_and_parent_flag():
    with pytest.raises(ValidationError, match='GRAPH_CREDENTIAL_REQUIRED'):
        Settings(_env_file=None, neo4j_enabled=True)
    with pytest.raises(ValidationError, match='GRAPH_DISABLED'):
        Settings(_env_file=None, neo4j_search_enabled=True)


def test_graph_settings_hide_password_and_allow_verified_remote():
    s = Settings(_env_file=None, neo4j_enabled=True, neo4j_password='private-test-password',
                 neo4j_uri_host='neo4j+s://remote.example')
    assert 'private-test-password' not in repr(s)
