import pytest

from tests.unit.test_catalog_pagination import seed_catalog

pytestmark = pytest.mark.postgres


def test_postgresql_catalog_unicode_pagination_matches_used_master(pg_container):
    seed_catalog(pg_container)
    service = pg_container.resource_lookups
    first = service.list_providers(page=1, size=7, query="ĐƠN VỊ")
    last = service.list_providers(page=158, size=7, query="Đơn vị")
    assert first["pagination"]["total"] == 1100
    assert first["items"][0]["id"] == "provider-0000"
    assert len(last["items"]) == 1 and last["items"][0]["id"] == "provider-1099"
    assert service.list_providers(page=999, size=7, query="Đơn vị")["pagination"]["total"] == 1100


def test_postgresql_catalog_full_casefold_matches_python_contract(pg_container):
    pg_container.sync.run(full=True)
    with pg_container.uow() as repo:
        for row in repo.find("master_learning_resources"):
            repo.update("master_learning_resources", row["id"], {"provider_name": "Straße İ ΟΣ"})
    for query in ("STRASSE", "i\u0307", "ος"):
        result = pg_container.resource_lookups.list_providers(page=1, size=7, query=query)
        assert len(result["items"]) == 2
