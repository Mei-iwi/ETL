import pytest
from pymongo import MongoClient
from sqlalchemy import create_engine

from data_sync_etl.adapters.mongo.component_content import (
    ComponentContentError,
    MongoComponentContent,
)
from data_sync_etl.adapters.persistence.component_ocr_lease import ComponentOCRLeaseQueue
from data_sync_etl.adapters.persistence.component_ocr_outbox import ComponentStateError


def test_component_prototype_cannot_bind_real_pymongo_database():
    client = MongoClient('mongodb://127.0.0.1:27017', connect=False)
    try:
        with pytest.raises(ComponentContentError, match='PROTOTYPE_DISABLED'):
            MongoComponentContent(client['etl_content'])
    finally:
        client.close()


def test_component_schema_prototype_refuses_real_postgres_or_sqlite_files(tmp_path):
    engines = [create_engine('postgresql+psycopg://localhost/unused'),
               create_engine('sqlite:///' + (tmp_path / 'not-created.db').as_posix())]
    for engine in engines:
        try:
            with pytest.raises(ComponentStateError, match='PROTOTYPE_DISABLED'):
                ComponentOCRLeaseQueue(engine, verify_mapping=lambda *_: True)
        finally:
            engine.dispose()
    assert not (tmp_path / 'not-created.db').exists()
