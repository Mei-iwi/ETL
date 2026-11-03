"""Production DI/API/worker tests with explicit IO doubles, without database DDL."""
import json
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from source_ocr_fakes import Content, MemoryUow, SourceEngine

from data_sync_etl import composition
from data_sync_etl.adapters.persistence.legacy_ocr_text import LegacyOCRText
from data_sync_etl.adapters.source import postgresql_ocr_write
from data_sync_etl.adapters.source.postgresql_ocr_queue import JOB_FIELDS, TASK_FIELDS
from data_sync_etl.adapters.source.registry import ConfiguredSourceRegistry
from data_sync_etl.api import routes
from data_sync_etl.application import source_ocr_runtime
from data_sync_etl.application.source_ocr_runtime import stable_id
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.domain.source_ids import SourceLocator, source_identity_hash
from data_sync_etl.domain.source_registry import SourceCapabilities, SourceRegistration


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), 'Photosynthesis converts solar energy into chemical energy in plants.')
        pdf = doc.tobytes(no_new_id=True)
    checksum = sha256(pdf).hexdigest()
    uow, source, content = MemoryUow(), SourceEngine(checksum), Content()
    user_locator = SourceLocator(backend='postgresql', namespace='source-a', entity_type='users', source_record_id='u1')
    uow.tables = {
        'resource_versions': {'mv1': dict(id='mv1', resource_id='r1', version_number=1, lifecycle_status='METADATA_ONLY',
                                       source_hash=checksum, processing_options={}, updated_at=now())},
        'master_learning_resources': {'r1': dict(id='r1', is_active=True, source_origin='POSTGRESQL',
            source_namespace='source-a', source_record_key='r1', publication_status='DRAFT', created_by='a1', deletion_revision=0)},
        'master_users': {'a1': dict(id='a1', is_active=True, source_origin='POSTGRESQL', source_record_key='u1',
            source_backend='postgresql', source_namespace='source-a', source_entity_type='users',
            source_identity_version=1, source_identity_hash=source_identity_hash(user_locator))},
        'source_file_imports': {'i1': dict(id='i1', resource_id='r1', target_version_id='mv1', source_version_id='v1',
            source_system='SRC_TEST', source_hash=checksum, source_version_number=1,
            source_lifecycle_status='APPROVED', source_resource_active=True, status='METADATA_ONLY')},
    }
    policy = dict(backend='postgresql', schema='public', entity_type='ocr_processing', namespace='source-a',
        tables=['ocr_jobs', 'ocr_page_tasks', 'content_units'],
        columns={'ocr_jobs': sorted(JOB_FIELDS | postgresql_ocr_write._JOB_COLUMNS),
                 'ocr_page_tasks': sorted(TASK_FIELDS),
                 'content_units': ['id', 'mongo_id', 'resource_version_id', 'parent_unit_id', 'unit_type', 'title',
                    'sequence_no', 'page_from', 'page_to', 'review_status', 'retrieval_eligible', 'created_at', 'updated_at']},
        actions=['select', 'insert', 'update'], allowed_roles=['ADMIN'], allowed_actor_ids=['master:a1'],
        allowed_version_ids=['v1'], allowed_lifecycles=['APPROVED'], statement_timeout_seconds=10)
    contract = tmp_path / 'owner-test-contract.json'
    contract.write_text(json.dumps(policy), encoding='utf-8')
    settings = Settings(_env_file=None, ocr_component_pipeline_enabled=True,
        source_ocr_contract_file=contract, source_namespace='source-a', source_adapter='postgresql',
        source_write_database_url='postgresql+psycopg://unit.invalid/source',
        source_database_url='postgresql+psycopg://unit.invalid/source',
        storage_root=tmp_path / 'storage', ocr_engine='native', index_auto_enabled=False)
    adapter = SimpleNamespace(locator=lambda entity, key: SourceLocator(
        backend='postgresql', namespace='source-a', entity_type=entity, source_record_id=key),
        get_versions=lambda ids: {key: dict(source_version_id=key, resource_id='r1', source_version_number=1,
            source_hash=source.versions[key]['source_hash'], resource_active=True, resource_publication_status='DRAFT',
            source_lifecycle_status=source.versions[key]['lifecycle_status']) for key in ids})
    adapter.get_version = lambda key: adapter.get_versions([key]).get(key)
    registration = SourceRegistration(source_id='SRC_TEST', namespace='source-a', backend='postgresql',
        adapter_type='TestDouble', capabilities=SourceCapabilities(read_content=True, process_content=True,
            index_derived_content=True, exact_version_lookup=True))
    registry = ConfiguredSourceRegistry([(registration, adapter)])
    engine = MagicMock()
    engine.dialect.name = 'postgresql'
    engine.connect.return_value.__enter__.return_value.execute.return_value.scalars.return_value.all.return_value = ['0020']
    monkeypatch.setattr(composition, 'create_source', lambda _: adapter)
    monkeypatch.setattr(composition, 'sessions', lambda _: None)
    monkeypatch.setattr(composition, 'SQLAlchemyUnitOfWork', lambda _: uow)
    mongo = MagicMock()
    mongo.__getitem__.return_value.list_collection_names.return_value = [
        'users', 'learning_resources', 'ocr_pages', 'mongo_content_units', 'content_bags']
    monkeypatch.setattr(composition, 'MongoClient', MagicMock(return_value=mongo))
    monkeypatch.setattr(source_ocr_runtime, 'make_engine', lambda _: source)
    serial = iter(range(1, 100))
    monkeypatch.setattr(postgresql_ocr_write, 'PostgreSQLSourceIDCounter', lambda _: None)
    monkeypatch.setattr(postgresql_ocr_write, 'SourceIDService', lambda _: SimpleNamespace(
        next_id=lambda prefix, **_kwargs: prefix + str(next(serial))))
    access = SearchAccess(scope='authenticated_admin', actor_id='master:a1')
    container = composition.Container(settings, engine, source_registry=registry,
                                      source_ocr_access_provider=lambda *_: access)
    pipeline = container.ocr
    pipeline.inventory._scope = lambda: (registration, None, {'id': 'a1', 'role_code': 'ADMIN'})
    container.documents = content
    pipeline.mongo = content
    pipeline.probe.reader = content
    reference = SimpleNamespace(ownership='SOURCE_BACKED', source_version_id='v1', expected_sha256=checksum,
                                resource_id='r1', filename='fixture.pdf', target_version_id='mv1', mime='application/pdf')
    container.search.authorize_source_processing_version = MagicMock()
    pipeline.resolver.resolve = MagicMock(return_value=reference)
    pipeline.resolver._capability_source_id = lambda _: 'SRC_TEST'
    pipeline.resolver.require_content_capabilities = MagicMock()
    opens = []

    @contextmanager
    def open_stream(*_args, **_kwargs):
        opens.append(True)
        yield BytesIO(pdf)
    pipeline.resolver.open_verified_stream = open_stream
    return SimpleNamespace(container=container, pipeline=pipeline, source=source, uow=uow,
                           content=content, opens=opens, access=access)


def test_production_container_api_worker_mongo_postprocess_and_reuse(runtime, monkeypatch):
    r, container = runtime, runtime.container
    assert container.worker.pipeline is r.pipeline
    assert container.process_version.ocr is r.pipeline
    assert not isinstance(container.ocr_text_store, LegacyOCRText)
    app = FastAPI()
    app.state.container = container
    app.include_router(routes.router)
    monkeypatch.setattr(routes, 'services', lambda _: container)
    monkeypatch.setattr(routes, 'search_access', lambda _: r.access)
    with TestClient(app) as client:
        created = client.post('/api/v1/resource-versions/mv1/process')
        assert created.status_code == 202, created.text
        job_id = created.json()['job_id']
        assert job_id != next(iter(r.source.jobs))
        assert len(r.source.tasks) == 1
        assert container.worker.once('worker-test') is True
        assert r.source.jobs[next(iter(r.source.jobs))]['status'] == 'COMPLETED'
        assert len(r.content.pages) == 1
        assert r.uow.get('ocr_jobs', job_id)['status'] == 'COMPLETED'
        assert r.uow.get('ocr_jobs', job_id)['triggered_by'] == 'a1'
        # Explicitly run finalization too so errors cannot be hidden by callback handling.
        result = container.processing_finalizer.finalize(job_id)
        assert result['status'] in ('CONTENT_READY', 'ALREADY_COMPLETED')
        assert len(r.content.units) == 1
        assert all(not Path(task['local_image_path']).exists() for task in r.source.tasks.values())
        assert not r.source.units  # A flat PDF page is not a fabricated SECTION.
        units = r.uow.find('content_units')
        assert units[0]['unit_type'] == 'PAGE_TEXT'
        assert r.content.units[units[0]['id']].text.startswith('Photosynthesis')
        assert all(task['local_image_path'] is None for task in r.uow.find('ocr_page_tasks'))
        assert 'original_text' not in json.dumps(r.pipeline.state('mv1'))
        assert all(segment['translation_status'] != 'pending' for segment in
                   r.pipeline.state('mv1')['pages']['1']['result_metadata']['paired_segments'])
        raw = client.get('/api/v1/resource-versions/mv1/ocr-pages/1', params={'job_id': job_id})
        assert raw.status_code == 200 and raw.json()['available'] is True
        assert raw.json()['text'].startswith('Photosynthesis')
        assert raw.headers['cache-control'] == 'private, no-store'
        status = client.get('/api/v1/resource-versions/mv1/etl-status')
        assert status.status_code == 200 and status.json()['processing_status'] == 'CONTENT_READY'
        assert '_source_ocr' not in status.json()['version']['processing_options']
        job_status = client.get(f'/api/v1/processing-jobs/{job_id}')
        assert job_status.status_code == 200 and job_status.json()['completed_pages'] == 1
        chunks = client.get('/api/v1/resource-versions/mv1/content-units')
        assert chunks.status_code == 200 and chunks.json()['items'][0]['text'].startswith('Photosynthesis')
        bilingual = client.get('/api/v1/resource-versions/mv1/bilingual')
        assert bilingual.status_code == 200, bilingual.text
        original = client.get('/api/v1/resource-versions/mv1/processing-source', headers={'Range': 'bytes=0-3'})
        assert original.status_code == 206 and original.content == b'%PDF'
        assert original.headers['cache-control'] == 'private, no-store'
        assert original.headers['content-disposition'] == 'inline; filename="hoc-lieu-mv1.pdf"'
        assert client.get('/api/v1/resource-versions/mv1/processing-source',
                          headers={'Range': 'bytes=999999999-'}).status_code == 416
        before = (len(r.opens), len(r.source.jobs), len(r.source.tasks), len(r.content.pages), len(r.content.units), len(units))
        second = client.post('/api/v1/resource-versions/mv1/process')
        assert second.status_code == 200, second.text
        assert second.json()['job_id'] == job_id
        assert (len(r.opens), len(r.source.jobs), len(r.source.tasks), len(r.content.pages), len(r.content.units),
                len(r.uow.find('content_units'))) == before
        # The second public entrypoint shares the same SOURCE owner and identity.
        other = client.post('/api/v1/resource-versions/mv1/ocr-jobs')
        assert other.status_code == 200 and other.json()['id'] == job_id


def test_lost_lease_cannot_persist_mongo_or_proofs(runtime):
    r = runtime
    r.pipeline.create_job('mv1', access=r.access)
    task = r.pipeline.claim('first')
    result, _ = r.pipeline.recognize(task)
    before = deepcopy(r.pipeline.state('mv1'))
    r.source.tasks[task['id']]['worker_id'] = 'replacement'
    with pytest.raises(DomainError, match='LEASE_LOST'):
        r.pipeline.succeed(task, result)
    assert not r.content.pages and r.pipeline.state('mv1') == before


def test_projection_stores_canonical_30_character_user_for_37_character_principal(runtime):
    r = runtime
    r.pipeline.create_job('mv1', access=r.access)
    master_user_id = 'F' + 'b' * 29
    state = r.pipeline.state('mv1')
    state['identity']['actor_id'] = f'master:{master_user_id}'
    r.pipeline.reader.authorize = lambda _identity: True
    with r.uow() as repo:
        user = repo.tables['master_users'].pop('a1')
        user['id'] = master_user_id
        repo.tables['master_users'][master_user_id] = user
        repo.tables['master_learning_resources']['r1']['created_by'] = master_user_id
        r.pipeline._save(repo, 'mv1', state)
    projected = r.pipeline.sync(state)
    assert len(state['identity']['actor_id']) == 37
    assert projected['triggered_by'] == master_user_id
    assert len(projected['triggered_by']) == 30


def test_mongo_committed_before_source_commit_is_reused_without_file_or_recognition(runtime, monkeypatch):
    r = runtime
    r.pipeline.create_job('mv1', access=r.access)
    task = r.pipeline.claim('first')
    result, _ = r.pipeline.recognize(task)
    r.pipeline._persist_page(task, result)  # Crash boundary: intent + Mongo durable, SOURCE incomplete.
    monkeypatch.setattr(r.pipeline.recognizer, '_recognize_bytes', lambda *_: pytest.fail('repeated OCR'))
    r.pipeline.resolver.open_verified_stream = lambda *_a, **_k: pytest.fail('reopened source')
    recognized, path = r.pipeline.recognize(task)
    assert recognized is None and path is None
    r.pipeline.succeed(task, recognized)
    assert r.source.tasks[task['id']]['status'] == 'COMPLETED'
    assert len(r.content.pages) == 1


def test_missing_page_lookup_is_exact_even_with_sparse_mongo_pages(runtime):
    r = runtime
    r.pipeline.create_job('mv1', access=r.access)
    task = r.pipeline.claim('first')
    assert r.pipeline.verified_page(r.pipeline.state('mv1'), 1) is None
    assert task['source_job_id'] == r.pipeline.state('mv1')['source_job_id']


def test_partial_raw_loss_only_recognizes_missing_page_and_reuses_all_other_artifacts(runtime, monkeypatch):
    r = runtime
    with pymupdf.open() as document:
        for text in ('Photosynthesis stores energy in plant cells.', 'Chlorophyll absorbs solar energy for photosynthesis.'):
            document.new_page().insert_text((72, 72), text)
        payload = document.tobytes(no_new_id=True)
    checksum = sha256(payload).hexdigest()
    r.uow.tables['resource_versions']['mv1']['source_hash'] = checksum
    r.source.versions['v1']['source_hash'] = checksum
    r.pipeline.resolver.resolve.return_value.expected_sha256 = checksum

    @contextmanager
    def open_stream(*_a, **_k):
        r.opens.append(True)
        yield BytesIO(payload)
    r.pipeline.resolver.open_verified_stream = open_stream
    job = r.pipeline.create_job('mv1', access=r.access)
    assert r.container.worker.once('first') and r.container.worker.once('second')
    assert len(r.content.pages) == len(r.content.units) == 2
    page_id = next(key for key, page in r.content.pages.items() if page.page_num == 2)
    del r.content.pages[page_id]
    calls = []
    recognize = r.pipeline.recognizer._recognize_bytes

    def recognize_missing(task, version, filename, data, **kwargs):
        calls.append(filename)
        return recognize(task, version, filename, data, **kwargs)
    monkeypatch.setattr(r.pipeline.recognizer, '_recognize_bytes', recognize_missing)
    r.pipeline.create_job('mv1', access=r.access)
    assert r.source.jobs[next(iter(r.source.jobs))]['completed_pages'] == 1
    assert r.container.worker.once('repair')
    assert calls == ['2.pdf']
    assert len(r.source.jobs) == 1 and len(r.source.tasks) == len(r.content.pages) == len(r.content.units) == 2
    assert all(not Path(task['local_image_path']).exists() for task in r.source.tasks.values())
    opens = len(r.opens)
    assert r.pipeline.create_job('mv1', access=r.access)['id'] == job['id']
    assert len(r.opens) == opens and calls == ['2.pdf']


def test_deterministic_ids_include_source_namespace_and_do_not_collide_with_source_ids():
    assert stable_id('SJ', 'one', 'job') != stable_id('SJ', 'two', 'job')
    assert len(stable_id('SL', 'namespace', 'version', 1, 10000)) == 30


def test_optional_source_ocr_environment_fields_accept_blank_template_values():
    settings = Settings(_env_file=None, source_ocr_contract_file='', source_ocr_actor_id='', source_ocr_role='')
    assert settings.source_ocr_contract_file is settings.source_ocr_actor_id is settings.source_ocr_role is None


def test_stale_recovery_fences_old_owner_and_honors_admission_limit(runtime):
    from datetime import timedelta
    r = runtime
    r.pipeline.create_job('mv1', access=r.access)
    first = r.pipeline.claim('first')
    assert r.pipeline.claim('second') is None
    r.source.tasks[first['id']]['heartbeat_at'] = now() - timedelta(seconds=r.pipeline.heartbeat_timeout + 1)
    assert r.pipeline.recover_stale()['recovered'] == 1
    second = r.pipeline.claim('second')
    assert second['worker_id'] != first['worker_id'] and second['retry_count'] == 1
    with pytest.raises(DomainError, match='LEASE_LOST'):
        r.pipeline.heartbeat(first)
    assert r.pipeline.heartbeat(second)


def test_mongo_failure_never_completes_source_and_manual_retry_keeps_job_identity(runtime, monkeypatch):
    r = runtime
    job = r.pipeline.create_job('mv1', access=r.access)
    insert = r.content.insert_source_ocr_page

    def unavailable(_document):
        raise DomainError('SOURCE_OCR_MONGO_UNAVAILABLE')
    monkeypatch.setattr(r.content, 'insert_source_ocr_page', unavailable)
    for _ in range(r.container.settings.ocr_max_retries):
        assert r.container.worker.once('failure-worker')
    source_job = next(iter(r.source.jobs.values()))
    assert source_job['status'] == 'FAILED' and source_job['completed_pages'] == 0
    assert not r.content.pages
    opens = len(r.opens)
    again = r.pipeline.create_job('mv1', access=r.access)
    assert again['status'] == 'FAILED' and len(r.opens) == opens
    app = FastAPI()
    app.state.container = r.container
    app.include_router(routes.router)
    monkeypatch.setattr(routes, 'services', lambda _: r.container)
    monkeypatch.setattr(routes, 'search_access', lambda _: r.access)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/processing-jobs/{job['id']}/retry")
        assert response.status_code == 202, response.text
        retried = response.json()
    assert retried['action'] == 'OCR_REQUEUED' and retried['job_id'] == job['id']
    monkeypatch.setattr(r.content, 'insert_source_ocr_page', insert)
    assert r.container.worker.once('retry-worker')
    assert next(iter(r.source.jobs.values()))['status'] == 'COMPLETED'
    assert len(r.source.jobs) == len(r.source.tasks) == len(r.content.pages) == 1


def test_generation_checksum_and_revocation_fail_closed(runtime):
    r = runtime
    r.pipeline.create_job('mv1', access=r.access)
    task = r.pipeline.claim('first')
    result, _ = r.pipeline.recognize(task)
    r.pipeline.succeed(task, result)
    state = r.pipeline.state('mv1')
    page_id = next(iter(r.content.pages))
    original = r.content.pages[page_id]
    for changes in ({'projection_generation': 2}, {'source_checksum': 'b' * 64}, {'raw_text': 'different'}):
        r.content.pages[page_id] = original.model_copy(update=changes)
        with pytest.raises(DomainError, match='PAGE_CONFLICT'):
            r.pipeline.verified_page(state, 1)
    r.content.pages[page_id] = original
    r.container.search.authorize_source_processing_version.side_effect = DomainError('SOURCE_OCR_REVOKED')
    with pytest.raises(DomainError, match='REVOKED'):
        r.pipeline.create_job('mv1', access=r.access)


def test_verified_empty_raw_is_a_successful_source_page_without_fake_content(runtime):
    from data_sync_etl.ports.processing import OCRResult
    r = runtime
    r.pipeline.create_job('mv1', access=r.access)
    task = r.pipeline.claim('blank-page')
    r.pipeline.succeed(task, OCRResult('', engine_name='test-empty-verified', metadata={}))
    page = r.pipeline.verified_page(r.pipeline.state('mv1'), 1)
    assert page is not None and page.raw_text == '' and page.char_count == 0
    assert r.pipeline.probe.probe(r.pipeline.snapshot(r.pipeline.state('mv1')), generation=1,
        processing_fingerprint=r.pipeline.state('mv1')['fingerprint'])['status'] == 'COMPLETE_VERIFIED'
    assert not r.content.units


def test_worker_automatically_schedules_with_the_single_approved_live_actor(runtime):
    r = runtime
    assert r.pipeline.state('mv1') is None
    assert r.container.worker.once('scheduler')
    assert len(r.source.jobs) == len(r.source.tasks) == 1
    assert len(r.content.pages) == 1
    assert not r.pipeline.schedule_authorized_source_job()
    assert r.container.processing_job_detail.run(next(iter(r.uow.tables['ocr_jobs'])))['processing_status'] == 'CONTENT_READY'


def test_missing_normalized_document_recovers_from_mongo_raw_without_reopening_source(runtime):
    r = runtime
    job = r.pipeline.create_job('mv1', access=r.access)
    assert r.container.worker.once('first')
    unit = r.uow.find('content_units')[0]
    r.uow.update('content_units', unit['id'], {'review_status': 'APPROVED', 'retrieval_eligible': True})
    del r.content.units[unit['id']]
    opens = len(r.opens)
    assert r.container.processing_job_detail.run(job['id'])['processing_status'] == 'FINALIZATION_PENDING'
    assert r.container.processing_finalizer.finalize(job['id'])['status'] == 'CONTENT_READY'
    assert r.container.processing_job_detail.run(job['id'])['processing_status'] == 'CONTENT_READY'
    assert len(r.opens) == opens and len(r.content.pages) == len(r.content.units) == 1
    assert r.content.units[unit['id']].projection_generation == 2
    restored = r.uow.get('content_units', unit['id'])
    assert restored['review_status'] == 'APPROVED' and restored['retrieval_eligible'] is True


def test_same_language_translation_does_not_copy_raw_into_master_checkpoint(runtime):
    r = runtime
    r.uow.tables['resource_versions']['mv1']['processing_options'] = {'source_language': 'vi', 'target_language': 'vi'}
    r.pipeline.create_job('mv1', access=r.access)
    assert r.container.worker.once('same-language')
    raw = next(iter(r.content.pages.values())).raw_text
    assert raw not in json.dumps(r.pipeline.state('mv1'), ensure_ascii=False)
    segments = r.container.bilingual.view('mv1')['items']
    assert segments and all(segment['translated_text'] == segment['original_text'] for segment in segments)


def test_incomplete_runtime_contract_is_rejected_before_source_connections(runtime, monkeypatch):
    path = runtime.container.settings.source_ocr_contract_file
    contract = json.loads(path.read_text(encoding='utf-8'))
    contract['columns']['ocr_page_tasks'].remove('heartbeat_at')
    path.write_text(json.dumps(contract), encoding='utf-8')
    monkeypatch.setattr(composition, 'create_source', lambda _: pytest.fail('opened source before contract validation'))
    with pytest.raises(DomainError, match='RUNTIME_CONTRACT_INCOMPLETE'):
        composition.Container(runtime.container.settings)


def test_worker_cannot_claim_before_staging_intent_is_complete(runtime, monkeypatch):
    r = runtime
    ensure = r.pipeline.writer.ensure_page_task

    def register_page(**kwargs):
        task_id = ensure(**kwargs)
        assert r.pipeline.claim('competing-worker') is None
        return task_id
    monkeypatch.setattr(r.pipeline.writer, 'ensure_page_task', register_page)
    r.pipeline.create_job('mv1', access=r.access)
    assert r.pipeline.claim('ready-worker') is not None


def test_legacy_writer_cannot_take_over_a_source_owned_version(runtime):
    from data_sync_etl.application.postprocess import Postprocessor
    r = runtime
    r.pipeline.create_job('mv1', access=r.access)
    opens = len(r.opens)
    with pytest.raises(DomainError, match='SOURCE_OCR_OWNER_REQUIRED'):
        r.pipeline.recognizer.create_job('mv1')
    with pytest.raises(DomainError, match='SOURCE_OCR_OWNER_REQUIRED'):
        Postprocessor(r.uow).run('mv1')
    assert len(r.opens) == opens and not r.content.pages


def test_legacy_processing_rejects_source_before_state_or_file_io(runtime):
    from data_sync_etl.application.postprocess import Postprocessor

    r = runtime
    with pytest.raises(DomainError, match='SOURCE_OCR_OWNER_REQUIRED'):
        r.pipeline.recognizer.create_job('mv1')
    with pytest.raises(DomainError, match='SOURCE_OCR_OWNER_REQUIRED'):
        Postprocessor(r.uow).run('mv1')
    assert not r.opens and not r.source.jobs and not r.uow.find('ocr_jobs')


@pytest.mark.parametrize('invalid', [None, 'actor', 'source_user', 'namespace', 'resource', 'version', 'hash', 'owner'])
def test_upload_receipt_policy_is_scoped_to_exact_owner_identity(runtime, invalid):
    r = runtime
    r.pipeline.contract = replace(r.pipeline.contract, version_ids=frozenset({'another-version'}),
                                  allow_etl_created_versions=True)
    r.pipeline.writer.contract = r.pipeline.contract
    resource = r.uow.tables['master_learning_resources']['r1']
    resource['source_record_key'] = 'sr1'
    receipt = dict(requested_by='master:a1', source_user_id='u1', source_namespace='source-a',
                   source_resource_id='sr1', source_version_id='v1', source_hash=r.source.versions['v1']['source_hash'])
    keys = dict(actor='requested_by', source_user='source_user_id', namespace='source_namespace',
                resource='source_resource_id', version='source_version_id', hash='source_hash')
    if invalid == 'owner':
        resource['created_by'] = 'another-user'
    elif invalid:
        receipt[keys[invalid]] = 'wrong'
    r.uow.source_version_writeback_receipt = lambda *_args, **_kwargs: receipt
    if invalid:
        with pytest.raises(DomainError, match='SOURCE_OCR_CONTRACT_DENIED'):
            r.pipeline.create_job('mv1', access=r.access)
        assert not r.source.jobs and not r.opens
    else:
        job = r.pipeline.create_job('mv1', access=r.access)
        assert job and len(r.source.jobs) == 1
        assert r.pipeline.contract.version_ids == frozenset({'another-version'})


def test_upload_receipt_never_expands_an_allowlist_without_contract_opt_in(runtime):
    r = runtime
    r.pipeline.contract = replace(r.pipeline.contract, version_ids=frozenset({'another-version'}))
    r.pipeline.writer.contract = r.pipeline.contract
    with pytest.raises(DomainError, match='SOURCE_OCR_CONTRACT_DENIED'):
        r.pipeline.create_job('mv1', access=r.access)
    assert not r.source.jobs and not r.opens


def test_external_source_job_without_provenance_is_observable_and_never_requeued(runtime):
    r = runtime
    stamp = now() - timedelta(days=1)
    external = dict(id='external-job', resource_version_id='v1', triggered_by='u1', status='COMPLETED',
                    total_pages=1, completed_pages=1, failed_pages=0, started_at=stamp,
                    completed_at=stamp, created_at=stamp, updated_at=stamp)
    r.source.jobs[external['id']] = external
    page = dict(id='external-page', job_id=external['id'], page_num=1, status='COMPLETED', retry_count=0,
                started_at=stamp, heartbeat_at=stamp, completed_at=stamp, updated_at=stamp)
    r.source.tasks[page['id']] = page
    with pytest.raises(DomainError, match='SOURCE_OCR_EXTERNAL_JOB_REQUIRES_PROVENANCE'):
        r.pipeline.create_job('mv1', access=r.access)
    assert r.source.jobs[external['id']] == external and r.source.tasks[page['id']] == page
    assert not r.opens and not r.uow.find('ocr_jobs')
    assert r.uow.get('source_file_imports', 'i1')['last_error_code'] == 'SOURCE_OCR_EXTERNAL_JOB_REQUIRES_PROVENANCE'


def test_concurrent_process_returns_same_pending_projection_before_staging(runtime, monkeypatch):
    r, held, concurrent = runtime, [], []

    @contextmanager
    def processing_lock(_version):
        if held:
            yield False
        else:
            held.append(True)
            try:
                yield True
            finally:
                held.pop()

    monkeypatch.setattr(r.uow, 'processing_lock', processing_lock)
    stage = r.pipeline._stage

    def observe_stage(*args):
        opens = len(r.opens)
        concurrent.append(r.pipeline.create_job('mv1', access=r.access))
        assert len(r.opens) == opens
        assert concurrent[0]['total_pages'] == 0
        return stage(*args)

    monkeypatch.setattr(r.pipeline, '_stage', observe_stage)
    first = r.pipeline.create_job('mv1', access=r.access)
    assert first['id'] == concurrent[0]['id']
    assert len(r.source.jobs) == len(r.source.tasks) == 1


def test_legacy_claim_cannot_open_or_complete_source_projection(runtime):
    r = runtime
    r.pipeline.create_job('mv1', access=r.access)
    task = r.uow.find('ocr_page_tasks')[0]
    opens = len(r.opens)
    with pytest.raises(DomainError, match='SOURCE_OCR_OWNER_REQUIRED'):
        r.pipeline.recognizer.recognize(task)
    with pytest.raises(DomainError, match='SOURCE_OCR_OWNER_REQUIRED'):
        r.pipeline.recognizer.succeed(task, None)
    assert len(r.opens) == opens
    assert not r.uow.find('ocr_page_results')


@pytest.mark.parametrize('code, expected', [
    ('SOURCE_OCR_LEGACY_VERSION_REQUIRES_EXPLICIT_PROVENANCE', 'SOURCE_OCR_LEGACY_PROVENANCE_REQUIRED'),
    ('SOURCE_OCR_' + 'X' * 60, 'SOURCE_OCR_SCHEDULE_FAILED'),
])
def test_scheduler_diagnostics_fit_existing_master_column(runtime, code, expected):
    runtime.pipeline.record_scheduling_failure('mv1', DomainError(code))
    row = runtime.uow.get('source_file_imports', 'i1')
    assert row['last_error_code'] == expected
    assert len(row['last_error_code']) <= 50
    assert row['attempts'] == 1 and row['status'] == 'RETRY_WAIT'


def test_terminal_projection_avoids_repeated_claim_and_recovery_but_reconcile_continues(runtime, monkeypatch):
    r = runtime
    job = r.pipeline.create_job('mv1', access=r.access)
    assert r.container.worker.once('worker')
    assert r.uow.get('ocr_jobs', job['id'])['status'] == 'COMPLETED'
    monkeypatch.setattr(r.pipeline.queue, 'claim', lambda *_a, **_k: pytest.fail('terminal job claimed'))
    monkeypatch.setattr(r.pipeline.queue, 'recover', lambda *_a, **_k: pytest.fail('terminal job recovered'))
    assert r.pipeline.claim('worker') is None
    assert r.pipeline.recover_stale() == {'recovered': 0}
    r.pipeline.reconcile_once()
    assert r.uow.get('ocr_jobs', job['id'])['status'] == 'COMPLETED'


def test_source_index_snapshot_uses_fresh_search_content_without_point_reads(runtime, monkeypatch):
    r = runtime
    r.pipeline.create_job('mv1', access=r.access)
    assert r.container.worker.once('phase-worker')
    documents = list(r.content.units.values())
    assert documents
    monkeypatch.setattr(r.pipeline.mongo, 'get_unit',
                        lambda *_a, **_k: pytest.fail('search phase repeated an Atlas point read'))

    def phase(version_id, *, limit=100, offset=0):
        assert version_id == 'mv1'
        return deepcopy(documents[offset:offset + limit])

    with r.uow() as repo:
        snapshot = r.container.indexing._snapshot(repo, 'mv1', content_reader=phase)
    assert {unit['id'] for unit in snapshot[2]} == {doc.id for doc in documents}
    assert all(unit['text'] for unit in snapshot[2])


@pytest.mark.parametrize('mutation', ['text', 'version', 'current', 'generation', 'page',
                                     'page_result', 'missing', 'duplicate'])
def test_source_search_content_phase_rechecks_proof_after_mutation(runtime, monkeypatch, mutation):
    r = runtime
    job = r.pipeline.create_job('mv1', access=r.access)
    assert r.container.worker.once('phase-worker')
    documents = list(r.content.units.values())
    monkeypatch.setattr(r.pipeline.mongo, 'get_unit',
                        lambda *_a, **_k: pytest.fail('search phase fell back to point reads'))

    def reader(rows):
        return lambda _version_id, limit=100, offset=0: deepcopy(rows[offset:offset + limit])

    with r.uow() as repo:
        complete = repo.get('ocr_jobs', job['id'])
        assert r.container.ocr_text_store.processing_state_from_content(
            repo, complete, reader(documents)) == 'CONTENT_READY'
        changes = {
            'text': {'text': 'changed after the initial read'},
            'version': {'resource_version_id': 'another-version'},
            'current': {'is_current': False},
            'generation': {'projection_generation': documents[0].projection_generation + 1},
            'page': {'page_from': documents[0].page_from + 1},
            'page_result': {'page_result_id': 'another-result'},
        }
        changed = ([documents[0].model_copy(update=changes[mutation]), *documents[1:]]
                   if mutation in changes else [] if mutation == 'missing'
                   else [*documents, documents[0]])
        assert r.container.ocr_text_store.processing_state_from_content(
            repo, complete, reader(changed)) != 'CONTENT_READY'


def test_wrong_resource_namespace_is_rejected_before_source_lookup_or_file_io(runtime, monkeypatch):
    r = runtime
    r.uow.tables['master_learning_resources']['r1']['source_namespace'] = 'other-tenant'
    monkeypatch.setattr(r.pipeline.resolver, 'resolve', lambda *_a: pytest.fail('wrong namespace resolved'))
    with pytest.raises(DomainError, match='SOURCE_OCR_NAMESPACE_MISMATCH'):
        r.pipeline.create_job('mv1', access=r.access)
    assert not r.source.jobs and not r.opens


def test_source_unavailability_has_bounded_scheduler_retries_and_no_duplicate_jobs(runtime):
    r = runtime
    opens = []

    @contextmanager
    def unavailable(*_a, **_k):
        opens.append(True)
        raise DomainError('SOURCE_UNAVAILABLE')
        yield
    r.pipeline.resolver.open_verified_stream = unavailable
    maximum = r.container.settings.source_file_import_max_attempts
    for _ in range(maximum):
        r.pipeline.inventory.next_scan = 0
        if r.uow.get('sync_checkpoints', r.pipeline.inventory.stream):
            r.uow.update('sync_checkpoints', r.pipeline.inventory.stream, {'last_id': None})
        with r.uow() as repo:
            options = dict(repo.get('resource_versions', 'mv1')['processing_options'])
            options.pop('_source_ocr_audit', None)
            repo.update('resource_versions', 'mv1', {'processing_options': options})
        assert not r.pipeline.schedule_authorized_source_job()
        r.uow.tables['source_file_imports']['i1']['next_retry_at'] = None
    assert not r.pipeline.schedule_authorized_source_job()
    assert len(opens) == maximum and len(r.source.jobs) == 1
    assert not r.source.tasks and not r.content.pages
    assert r.uow.get('source_file_imports', 'i1')['status'] == 'RETRY_EXHAUSTED'


def test_docx_outline_writes_only_real_structural_source_nodes(runtime):
    from docx import Document
    r = runtime
    document, output = Document(), BytesIO()
    document.add_heading('Plant Biology', level=1)
    document.add_paragraph('Photosynthesis stores energy in sugars and releases oxygen from water.')
    document.add_heading('Photosynthesis', level=2)
    document.add_paragraph('Chloroplasts absorb sunlight through chlorophyll pigments.')
    document.save(output)
    payload = output.getvalue()
    checksum = sha256(payload).hexdigest()
    r.uow.tables['resource_versions']['mv1']['source_hash'] = checksum
    r.source.versions['v1']['source_hash'] = checksum
    reference = r.pipeline.resolver.resolve.return_value
    reference.expected_sha256, reference.filename = checksum, 'fixture.docx'

    @contextmanager
    def open_stream(*_a, **_k):
        yield BytesIO(payload)
    r.pipeline.resolver.open_verified_stream = open_stream
    job = r.pipeline.create_job('mv1', access=r.access)
    assert r.container.worker.once('docx-worker')
    r.container.processing_finalizer.finalize(job['id'])
    assert {row['unit_type'] for row in r.source.units.values()} == {'CHAPTER', 'LESSON'}
    assert all(row['review_status'] == 'PENDING' for row in r.source.units.values())
    units = r.uow.find('content_units', order=('sequence_no',))
    assert any(unit['unit_type'] == 'DOCX_TEXT' for unit in units)
    assert len(r.source.units) < len(units)
    assert r.container.indexing._version(r.uow, 'mv1')[1] == 'CONTENT_READY'
    r.pipeline.sync(r.pipeline.state('mv1'))
    assert len(r.uow.find('content_units')) == len(units)


def test_source_structure_commit_before_master_commit_recovers_without_duplicate_units(runtime, monkeypatch):
    from docx import Document
    r = runtime
    document, output = Document(), BytesIO()
    document.add_heading('Plant Biology', level=1)
    document.add_paragraph('Photosynthesis stores chemical energy in plants.')
    document.save(output)
    payload = output.getvalue()
    checksum = sha256(payload).hexdigest()
    r.uow.tables['resource_versions']['mv1']['source_hash'] = checksum
    r.source.versions['v1']['source_hash'] = checksum
    reference = r.pipeline.resolver.resolve.return_value
    reference.expected_sha256, reference.filename = checksum, 'fixture.docx'

    @contextmanager
    def open_stream(*_a, **_k):
        yield BytesIO(payload)
    r.pipeline.resolver.open_verified_stream = open_stream
    job = r.pipeline.create_job('mv1', access=r.access)
    task = r.pipeline.claim('first')
    result, _ = r.pipeline.recognize(task)
    r.pipeline.succeed(task, result)
    save = r.pipeline._save
    interrupted = False

    def interrupt_master_commit(repo, version_id, state):
        nonlocal interrupted
        if state['units'] and not interrupted:
            interrupted = True
            raise RuntimeError('synthetic interruption after SOURCE structure commit')
        return save(repo, version_id, state)
    monkeypatch.setattr(r.pipeline, '_save', interrupt_master_commit)
    with pytest.raises(DomainError, match='Postprocess failed'):
        r.container.postprocess.run('mv1')
    assert len(r.source.units) == 1 and not r.uow.find('content_units')
    r.pipeline.sync(r.pipeline.state('mv1'))
    monkeypatch.setattr(r.pipeline, '_save', save)
    assert r.container.processing_finalizer.finalize(job['id'])['status'] == 'CONTENT_READY'
    assert len(r.source.units) == 1 and len(r.uow.find('content_units')) == 2
    before = deepcopy(r.uow.find('content_units'))
    r.pipeline.sync(r.pipeline.state('mv1'))
    r.pipeline.sync(r.pipeline.state('mv1'))
    assert r.uow.find('content_units') == before


def test_source_index_uses_mongo_text_and_builds_real_lexical_bag_with_1024_vector_double(runtime, monkeypatch):
    from data_sync_etl.application.search import SearchService
    from data_sync_etl.domain.indexing import digest
    from tests.indexing_support import MemoryBags, ModelDouble
    r = runtime

    class Reader:
        def units(self, version_id, *, offset=0, limit=100):
            docs = sorted([doc for doc in r.content.units.values() if doc.resource_version_id == version_id], key=lambda d: d.id)
            return deepcopy(docs[offset:offset + limit])

        def get_unit(self, unit_id):
            return deepcopy(r.content.units.get(unit_id))

        def update_leaf_keywords(self, observed, keywords):
            r.content.units[observed.id] = observed.model_copy(update={'keywords': keywords})

        def content_fingerprint(self, version_id, *, generation=None):
            return digest([(doc.id, doc.text, doc.keywords, doc.projection_generation) for doc in self.units(version_id)])

    class EmbeddingDouble(ModelDouble):
        dimension = 1024

        def embed(self, _text):
            return [1.] + [0.] * 1023

    index = r.container.indexing
    index.reader, index.embedding = Reader(), EmbeddingDouble()
    index.bags = MemoryBags(index.reader)
    index.bags.dimension = 1024
    r.container.settings.index_meaning_bags_enabled = False
    r.container.settings.index_embedding_dimension = 1024
    r.container.settings.search_metadata_enabled = False
    r.container.settings.index_auto_enabled = True
    job = r.pipeline.create_job('mv1', access=r.access)
    assert r.container.worker.once('index-worker')
    r.container.processing_finalizer.finalize(job['id'])
    assert index.once('mv1'), [(s['stage'], s['status'], s.get('error_message')) for s in r.uow.find('etl_stage_runs')]
    assert index.status('mv1')['index_status'] == 'READY'
    bag = next(iter(index.bags.rows.values()))
    assert len(bag.embedding) == 1024
    assert dict(zip(bag.lexical.terms, bag.lexical.frequencies, strict=True))['photosynthesis'] == 1
    assert bag.embedding_text == ' | '.join(bag.keywords)
    search = SearchService(index, r.container.settings, index.bags)
    index.bags.search_candidates = lambda ids, **_kw: [deepcopy(bag)] if 'mv1' in ids else []
    metadata = dict(resource_id='r1', resource_title='Plant biology', provider_name=None,
                    resource_type_code='PDF', version_id='mv1', version_number=1)
    r.uow.search_versions = lambda *_a, **_k: ([metadata] if r.uow.get('master_learning_resources', 'r1')['publication_status'] == 'PUBLISHED' else [])
    assert search.search('photosynthesis', method='bow', access=r.access)['items'] == []
    r.uow.update('master_learning_resources', 'r1', {'publication_status': 'PUBLISHED'})
    result = search.search('photosynthesis', method='bow', access=r.access)
    assert result['items'] and result['items'][0]['resource_id'] == 'r1'
    for method in ('vector', 'hybrid'):
        result = search.search('photosynthesis', method=method, access=r.access)
        assert result['items'] and result['items'][0]['resource_id'] == 'r1'
    app = FastAPI()
    r.container.search = search
    app.state.container = r.container
    app.include_router(routes.router)
    monkeypatch.setattr(routes, 'search_access', lambda _: r.access)
    r.uow.search_unit_texts = lambda _ids: pytest.fail('SOURCE search must hydrate Mongo text')
    with TestClient(app) as client:
        response = client.post('/api/v1/search', json={'query': 'photosynthesis', 'method': 'bow'})
        assert response.status_code == 200, response.text
        assert response.json()['items'][0]['resource_id'] == 'r1'
    # Public full-text permission is deliberately unchanged.
    assert search.search('photosynthesis', method='bow', access=SearchAccess())['items'] == []
