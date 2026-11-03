from pathlib import Path

import yaml


def test_master_only_compose_has_one_database_and_no_local_source_or_mongo():
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / 'deploy/compose.master-only.yml').read_text(encoding='utf-8'))
    assert set(compose['services']) == {'master-postgres'}
    assert compose['services']['master-postgres']['image'] == 'postgres:16-bookworm'
    assert compose['services']['master-postgres']['volumes'] == ['master-only-data:/var/lib/postgresql/data']
    assert 'secrets' not in compose


def test_root_compose_default_only_starts_database_and_migration_is_pinned_0020():
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / 'docker-compose.yml').read_text(encoding='utf-8'))
    services = compose['services']
    assert not services['postgres'].get('profiles')
    assert services['master-migrate']['profiles'] == ['ops']
    assert services['master-migrate']['command'][-1] == '0020'
    assert services['source-postgres']['profiles'] == ['local-dev']
    assert services['mongo']['profiles'] == ['local-dev']
