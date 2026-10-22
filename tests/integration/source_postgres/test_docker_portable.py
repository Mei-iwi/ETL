"""Docker-only native/real-model acceptance on UUID-owned PostgreSQL/Mongo fixtures."""
import os
from hashlib import sha256
from pathlib import Path

import pytest
from pymongo import MongoClient
from sqlalchemy import text

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.adapters.mongo.content_store import MongoContentStore
from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore, MongoContentReader
from data_sync_etl.adapters.mongo.schema import initialize
from data_sync_etl.adapters.ocr.engines import TesseractOCREngine
from data_sync_etl.application.indexing import IndexingService
from data_sync_etl.domain.search_access import SearchAccess
from tests.integration.source_postgres.test_file_import_flow import setup_import
from tests.unit.test_multiformat import sample

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(os.environ.get('ETL_DOCKER_NATIVE_TEST') != '1', reason='Explicit Docker fixture run required')]


@pytest.fixture(scope='module')
def portable_model():
    from deploy.runtime.model import DIMENSION, MODEL_ID, REVISION
    model = LocalSentenceEmbedding(Path('/opt/models/minilm'), MODEL_ID, REVISION, DIMENSION)
    model.prepare()
    assert len(model.embed('Học sinh học phân số.')) == 384
    return model


@pytest.mark.parametrize('extension', ['.pdf', '.docx', '.xlsx', '.pptx', '.txt', '.csv', '.png', '.jpg', '.jpeg'])
def test_source_pipeline_real_databases_model_and_ocr(source, pg_container, mongo_test_database, tmp_path, portable_model, extension):
    c = pg_container
    c.settings.source_reference_enabled = c.settings.source_temporary_files_enabled = True
    c.settings.source_temp_root = tmp_path / 'source-temp'
    c.settings.chunk_size_words, c.settings.chunk_overlap_words = 20, 4
    c.settings.index_embedding_dimension = 384
    c.ocr.engine = TesseractOCREngine('/usr/bin/tesseract', 'vie+eng', 60)
    original = setup_import(source, c, tmp_path)
    c.source_resolver.source = source.adapter
    content = original.with_suffix(extension)
    if extension == '.pdf':
        content.write_bytes(original.read_bytes())
    else:
        content.write_bytes(sample(extension))
    before = content.read_bytes()
    with source.engine.begin() as connection:
        connection.execute(text("UPDATE resource_versions SET storage_object_key=:key,source_hash=:hash,updated_at=now() WHERE id='source-local-1'"),
                           {'key': content.name, 'hash': sha256(before).hexdigest()})
    with MongoClient(mongo_test_database[0], serverSelectionTimeoutMS=3000) as client:
        database = client[mongo_test_database[1]]
        assert database.name.startswith('etl_test_') and len(database.name) == 41
        assert initialize(database, dimension=384, apply=True)['ready']
        c.mongo_projector.documents = MongoContentStore(database, invalidate_content_bags=False)
        c.indexing = IndexingService(c.uow, MongoContentReader(database), portable_model, c.settings, bags=MongoBagStore(database, 384))
        c.processing_finalizer.on_content_ready = c.indexing.enqueue_auto
        c.worker.reconcile = c.reconcile_processing
        c.search.indexing, c.search.bags = c.indexing, c.indexing.bags
        assert c.source_file_worker.once('portable-file')
        with c.uow() as repo:
            row = repo.find('source_file_imports', {})[0]
        version_id = row['target_version_id']
        assert version_id, row
        for _ in range(30):
            if not c.worker.once('portable-ocr'):
                break
        assert c.orchestrator.status(version_id)['processing_status'] == 'CONTENT_READY'
        assert c.indexing.enqueue(version_id)
        assert c.indexing.once()
        assert c.indexing.status(version_id)['index_status'] == 'READY'
        bags = list(database.content_bags.find({}))
        assert bags and all(len(bag['embedding']) == 384 for bag in bags)
        grant = SearchAccess('local_demo')  # Explicit service-level test grant, never HTTP/container bypass.
        query = bags[0]['embedding_text'].strip()[:100]
        assert query
        for method in ('bow', 'vector', 'hybrid'):
            result = c.search.search(query, method, access=grant)
            assert result['total'] >= 1
        with c.uow() as repo:
            assert repo.count('content_units', {'resource_version_id': version_id}) > 0
        assert set(database.list_collection_names()) == {'users', 'learning_resources', 'ocr_pages', 'mongo_content_units', 'content_bags'}
    assert content.read_bytes() == before
    assert not list((c.storage.root / 'resources').rglob('*'))
    assert not list(c.settings.source_temp_root.glob('attempt-*'))
