from sqlalchemy import insert

from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyRepository
from data_sync_etl.db.models import MasterLearningResource


def seed_catalog(container):
    container.sync.run(full=True)
    with container.uow() as repo:
        template = repo.get("master_learning_resources", "resource-1")
        rows = [{**template, "id": f"catalog-{i:04}", "provider_id": f"provider-{i:04}",
                 "provider_name": f"Đơn vị {i:04}"} for i in range(1100)]
        repo.session.execute(insert(MasterLearningResource), rows)


def test_sql_catalog_1100_rows_unicode_pages_sparse_and_no_find_all(container, monkeypatch):
    seed_catalog(container)
    def no_unbounded_read(*args, **kwargs):
        raise AssertionError("Catalog must not use Repository.find(all resources)")
    monkeypatch.setattr(SQLAlchemyRepository, "find", no_unbounded_read)
    service = container.resource_lookups
    # Container property is verified below; no fake repository or SQL response.
    first = service.list_providers(page=1, size=7, query="ĐƠN VỊ")
    last = service.list_providers(page=158, size=7, query="Đơn vị")
    sparse = service.list_providers(page=999, size=7, query="Đơn vị")
    empty = service.list_providers(page=1, size=7, query="%_")
    assert first["pagination"]["total"] == last["pagination"]["total"] == 1100
    assert len(first["items"]) == 7 and len(last["items"]) == 1
    assert first["items"][0]["name"] == "Đơn vị 0000"
    assert sparse["items"] == [] and sparse["pagination"]["total"] == 1100
    assert empty["items"] == [] and empty["pagination"]["total_pages"] == 0
