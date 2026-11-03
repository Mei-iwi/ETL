"""Exercise provisioned dev SOURCE OCR through authenticated HTTP and real IO.

No migrations, schema bootstrap, fake vectors or credentials in output. Recovery
mode mutates only the tagged, explicitly selected fixture's task heartbeats.
The API/worker and normal users sync must already be running.
"""
import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from sqlalchemy import text

from data_sync_etl.composition import Container
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--credentials-file', type=Path, required=True)
    parser.add_argument('--fixture-name', default='scan3')
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--recovery', action='store_true')
    parser.add_argument('--resume-empty', action='store_true')
    parser.add_argument('--readback', action='store_true')
    parser.add_argument('--postprocess-failure', action='store_true', help='Inject one failure before Mongo unit write on the tagged fixture')
    parser.add_argument('--integrity', action='store_true', help='Verify fail-closed provenance, fingerprint and namespace on an owned fixture')
    parser.add_argument('--first-unit-crash', action='store_true', help='Exercise actual first unit writes before Mongo and after Mongo/before SQL commit; stop other OCR workers first')
    parser.add_argument('--partial', action='store_true')
    parser.add_argument('--metrics-file', type=Path)
    parser.add_argument('--fixture-acl-failures', action='store_true')
    parser.add_argument('--pause-file', type=Path)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    settings = Settings()
    if settings.app_environment not in ('development', 'test') or not settings.ocr_component_pipeline_enabled:
        raise RuntimeError('DEV_SOURCE_RUNTIME_REQUIRED')
    fixtures = json.loads(args.fixtures.read_text(encoding='utf-8'))
    if isinstance(fixtures, dict):
        fixtures = [{**fixtures, 'fixture_name': args.fixture_name}]
    fixture = next(item for item in fixtures if item['fixture_name'] == args.fixture_name)
    version_id = fixture['master_version_id']
    credentials = json.loads(args.credentials_file.read_text(encoding='utf-8'))
    headers = {'Host': httpx.URL(settings.auth_origin).netloc.decode(), 'Origin': settings.auth_origin,
               'X-ETL-Proxy-Key': Path(settings.auth_proxy_key_file).read_text().strip()}
    evidence = (json.loads(args.evidence.read_text(encoding='utf-8'))
                if args.resume_empty and args.evidence.is_file() else {})

    def record(key, value):
        evidence[key] = value
        args.evidence.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        print(key, value, flush=True)

    with httpx.Client(base_url=args.base_url, headers=headers, timeout=240) as api:
        csrf = api.get('/api/v1/auth/csrf')
        csrf.raise_for_status()
        api.headers['X-CSRF-Token'] = csrf.json()['csrf_token']
        login = api.post('/api/v1/auth/login', json=credentials)
        assert login.status_code == 200, 'FIXTURE_LOGIN_FAILED'
        actor = api.get('/api/v1/auth/me').json()
        assert actor['role'] == 'ADMIN' and actor['id'].startswith('master:'), 'SOURCE_ACTOR_REQUIRED'
        record('authenticated_source_admin', True)
        if args.first_unit_crash:
            verify_first_unit_crash(settings, fixture, record)
            return
        if args.integrity:
            verify_integrity(api, settings, fixture, args, record)
            return
        if args.postprocess_failure:
            verify_postprocess_failure(settings, fixture, record)
            return
        if args.partial:
            verify_partial(api, settings, fixture, args, record)
            return
        if args.readback:
            verify_readback(api, settings, fixture, args, record)
            return
        if args.recovery or args.resume_empty:
            assert args.pause_file and args.pause_file.is_file(), 'OWNED_TEST_WORKER_PAUSE_REQUIRED'
        process = api.post(f'/api/v1/resource-versions/{version_id}/process')
        record('process_http', process.status_code)
        assert process.status_code in (200, 202), 'PROCESS_HTTP_FAILED'
        job_id = process.json()['job_id']
        record('master_job_id', job_id)
        if not (args.recovery or args.resume_empty):
            return
        container = Container(settings.model_copy(update={'ocr_max_active': 2}))
        pipeline = container.ocr
        try:
            with container.uow() as repo:
                version = repo.get('resource_versions', version_id)
                resource = repo.get('master_learning_resources', version['resource_id'])
            assert resource['title'].startswith('CODEX E2E SOURCE OCR '), 'TAGGED_FIXTURE_REQUIRED'
            assert resource['publication_status'] == 'DRAFT'
            state = pipeline.state(version_id)
            identity, source_job = state['identity'], state['source_job_id']
            assert identity['source_version_id'] == fixture['source_version_id']
            with pipeline.source_engine.connect() as conn:
                assert conn.execute(text('SELECT current_database()')).scalar_one() == 'Learning_Resources'
                assert conn.execute(text('SELECT count(*) FROM ocr_page_tasks WHERE job_id=:job'),
                                    {'job': source_job}).scalar_one() == 3

            def claim(worker):
                task = pipeline.queue.claim(identity, source_job, worker,
                    allow_claim=lambda: bool(pipeline.state(version_id).get('scheduled')))
                if task:
                    return {**task, 'master_version_id': version_id, 'job_id': job_id}
                return None

            if args.resume_empty:
                with pipeline.source_engine.begin() as conn:
                    conn.execute(text(
                        "UPDATE ocr_page_tasks SET heartbeat_at=now()-interval '1 day' "
                        "WHERE job_id=:job AND page_num=3 AND status='PROCESSING'"
                    ), {'job': source_job})
                pipeline.queue.recover(identity, source_job)
                snapshot = pipeline.snapshot(pipeline.state(version_id))
                if snapshot['job']['status'] == 'FAILED':
                    pipeline.queue.retry(identity, source_job)
                blank = claim('fixture-worker-empty-retry')
                assert blank and blank['page_num'] == 3, 'EMPTY_PAGE_RETRY_NOT_AVAILABLE'
                result, path = pipeline.recognize(blank)
                assert result is not None and result.text == '', 'EXPECTED_VERIFIED_EMPTY_PAGE'
                pipeline.succeed(blank, result, path)
                record('real_empty_page_completed', True)
                snapshot = pipeline.snapshot(pipeline.state(version_id))
                assert snapshot['job']['status'] == 'COMPLETED'
                assert snapshot['job']['completed_pages'] == 3 and snapshot['job']['failed_pages'] == 0
                record('source_terminal_counts', {'total': 3, 'completed': 3, 'failed': 0, 'tasks': len(snapshot['pages'])})
                return

            with ThreadPoolExecutor(max_workers=2) as pool:
                tasks = list(pool.map(claim, ['fixture-worker-a', 'fixture-worker-b']))
            assert all(tasks) and tasks[0]['id'] != tasks[1]['id']
            assert tasks[0]['worker_id'] != tasks[1]['worker_id']
            record('concurrent_claims_distinct', sorted(task['page_num'] for task in tasks))
            first, old = sorted(tasks, key=lambda task: task['page_num'])
            assert pipeline.heartbeat(first)
            result, path = pipeline.recognize(first)
            pipeline.succeed(first, result, path)
            record('real_scan_page_completed', first['page_num'])

            def expire(task):
                with pipeline.source_engine.begin() as conn:
                    changed = conn.execute(text(
                        "UPDATE ocr_page_tasks SET heartbeat_at=now()-interval '1 day' "
                        "WHERE id=:task AND job_id=:job AND worker_id=:owner AND status='PROCESSING'"
                    ), {'task': task['id'], 'job': source_job, 'owner': task['worker_id']})
                    assert changed.rowcount == 1
                assert pipeline.queue.recover(identity, source_job) == 1

            expire(old)
            replacement = claim('fixture-worker-recovered')
            assert replacement['id'] == old['id'] and replacement['worker_id'] != old['worker_id']
            callbacks = []
            try:
                pipeline.queue.transition(identity, old, complete=True, before_complete=lambda: callbacks.append(True))
            except DomainError as exc:
                assert str(exc) == 'SOURCE_OCR_LEASE_LOST'
            else:
                raise AssertionError('STALE_OWNER_ACCEPTED')
            assert not callbacks
            record('stale_owner_fenced_before_mongo', True)

            result, _ = pipeline.recognize(replacement)

            def commit_mongo_then_crash():
                pipeline._persist_page(replacement, result)
                raise RuntimeError('CONTROLLED_FIXTURE_CRASH_AFTER_MONGO')

            try:
                pipeline.queue.transition(identity, replacement, complete=True, before_complete=commit_mongo_then_crash)
            except RuntimeError as exc:
                assert str(exc) == 'CONTROLLED_FIXTURE_CRASH_AFTER_MONGO'
            with pipeline.source_engine.connect() as conn:
                status = conn.execute(text('SELECT status FROM ocr_page_tasks WHERE id=:id'),
                                      {'id': replacement['id']}).scalar_one()
            assert status == 'PROCESSING'
            assert pipeline.verified_page(pipeline.state(version_id), replacement['page_num']) is not None
            record('mongo_durable_source_transaction_rolled_back', True)
            expire(replacement)
            recovered = claim('fixture-worker-after-crash')
            assert recovered['id'] == replacement['id']
            result, path = pipeline.recognize(recovered)
            assert result is None and path is None
            pipeline.succeed(recovered, result, path)
            record('crash_retry_reused_verified_mongo', True)
            blank = claim('fixture-worker-empty-page')
            assert blank and blank['page_num'] == 3
            result, path = pipeline.recognize(blank)
            assert result is not None and result.text == ''
            pipeline.succeed(blank, result, path)
            record('real_empty_page_completed', True)
            snapshot = pipeline.snapshot(pipeline.state(version_id))
            assert snapshot['job']['status'] == 'COMPLETED'
            assert snapshot['job']['completed_pages'] == 3 and snapshot['job']['failed_pages'] == 0
            assert len(snapshot['pages']) == len({page['page_num'] for page in snapshot['pages']}) == 3
            record('source_terminal_counts', {'total': 3, 'completed': 3, 'failed': 0, 'tasks': 3})
        finally:
            container.close()
            if args.pause_file:
                args.pause_file.unlink(missing_ok=True)


def verify_first_unit_crash(settings, fixture, record):
    container = Container(settings)
    version_id = fixture['master_version_id']
    original = container.documents.upsert_content_unit
    recognize = container.ocr.recognizer._recognize_bytes
    engine_recognize = container.ocr.recognizer.engine.recognize_bytes
    calls = {'unit_writes': 0, 'recognizer': 0, 'engine': 0}
    try:
        with container.uow() as repo:
            resource = repo.get('master_learning_resources', fixture['master_resource_id'])
            assert resource['title'] == 'CODEX E2E SOURCE OCR unitcrash 20261103'
            assert resource['publication_status'] == 'DRAFT'
            assert not repo.find('content_units', {'resource_version_id': version_id}, limit=1)
        collection = container.documents.database.mongo_content_units
        assert collection.count_documents({'resource_version_id': version_id}) == 0

        def interrupted(document):
            assert document['resource_version_id'] == version_id, 'FAULT_SCOPE_VIOLATION'
            calls['unit_writes'] += 1
            if calls['unit_writes'] == 1:
                raise RuntimeError('controlled interruption before first normalized Mongo insert')
            result = original(document)
            if calls['unit_writes'] == 2:
                raise RuntimeError('controlled interruption after Mongo insert before first SQL unit commit')
            return result

        def recognize_observed(*args, **kwargs):
            calls['recognizer'] += 1
            return recognize(*args, **kwargs)

        def engine_observed(*args, **kwargs):
            calls['engine'] += 1
            return engine_recognize(*args, **kwargs)

        container.documents.upsert_content_unit = interrupted
        container.ocr.recognizer._recognize_bytes = recognize_observed
        container.ocr.recognizer.engine.recognize_bytes = engine_observed

        def advance(target):
            deadline = time.monotonic() + 600
            while calls['unit_writes'] < target and time.monotonic() < deadline:
                container.worker.once('source-first-unit-crash')
            assert calls['unit_writes'] == target, 'EXPECTED_NORMALIZED_UNIT_FAULT_NOT_REACHED'
            with container.uow() as repo:
                units = repo.find('content_units', {'resource_version_id': version_id})
                job = repo.find('ocr_jobs', {'resource_version_id': version_id}, limit=1)[0]
                status = container.ocr_text_store.processing_state(repo, job)
            assert not units and status == 'FINALIZATION_FAILED'
            assert job['status'] == 'COMPLETED' and job['completed_pages'] == job['total_pages'] == 1
            assert collection.count_documents({'resource_version_id': version_id}) == target - 1
            return job

        job = advance(1)
        assert calls['recognizer'] == calls['engine'] == 1
        record('first_unit_pre_mongo_failure_sql0_mongo0_not_ready', True)
        advance(2)
        durable_id = collection.find_one({'resource_version_id': version_id})['_id']
        assert calls['recognizer'] == calls['engine'] == 1
        record('first_unit_mongo_durable_sql_rollback_sql0_mongo1_not_ready', True)
        container.documents.upsert_content_unit = original
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            container.worker.once('source-first-unit-recovery')
            with container.uow() as repo:
                status = container.ocr_text_store.processing_state(repo, repo.get('ocr_jobs', job['id']))
                units = repo.find('content_units', {'resource_version_id': version_id})
            if status == 'CONTENT_READY':
                break
        else:
            raise AssertionError('FIRST_UNIT_RECOVERY_NOT_READY')
        assert len(units) == 1 and units[0]['id'] == durable_id
        assert collection.count_documents({'resource_version_id': version_id}) == 1
        assert calls['recognizer'] == calls['engine'] == 1
        state = container.ocr.state(version_id)
        assert container.documents.database.ocr_pages.count_documents({'job_id': state['source_job_id']}) == 1
        record('first_unit_crash_recovery_same_id_no_reocr', {'unit_id': durable_id,
               'recognizer_total': calls['recognizer'], 'tesseract_total': calls['engine'], 'processing_status': status})
    finally:
        container.documents.upsert_content_unit = original
        container.ocr.recognizer._recognize_bytes = recognize
        container.ocr.recognizer.engine.recognize_bytes = engine_recognize
        container.close()


def verify_integrity(api, settings, fixture, args, record):
    container = Container(settings)
    version_id = fixture['master_version_id']
    try:
        with container.uow() as repo:
            resource = repo.get('master_learning_resources', fixture['master_resource_id'])
            version = repo.get('resource_versions', version_id)
            imports = repo.find('source_file_imports', {'target_version_id': version_id}, limit=2)
            job = repo.find('ocr_jobs', {'resource_version_id': version_id}, limit=1)[0]
        assert resource['title'].startswith('CODEX E2E SOURCE OCR ') and resource['publication_status'] == 'DRAFT'
        assert len(imports) == 1 and job['status'] == 'COMPLETED'
        options = version['processing_options']
        state = options['_source_ocr']

        def source_snapshot():
            with container.source_ocr_engine.connect() as conn:
                result = [dict(row) for row in conn.execute(text('SELECT * FROM ocr_jobs WHERE id=:id'),
                          {'id': state['source_job_id']}).mappings()]
                result.extend(dict(row) for row in conn.execute(text('SELECT * FROM ocr_page_tasks WHERE job_id=:id ORDER BY page_num,id'),
                              {'id': state['source_job_id']}).mappings())
                result.extend(dict(row) for row in conn.execute(text('SELECT * FROM content_units WHERE resource_version_id=:id ORDER BY id'),
                              {'id': state['identity']['source_version_id']}).mappings())
                return result

        before_source = source_snapshot()
        args.evidence.with_name('fixture-integrity-backup.json').write_text(
            json.dumps({'resource_namespace': resource['source_namespace'], 'version_options': options,
                        'source_import': imports[0]}, default=str, indent=2), encoding='utf-8')
        try:
            changed = json.loads(json.dumps(options))
            changed['_source_ocr'].pop('source_job_id')
            with container.uow() as repo:
                repo.update('resource_versions', version_id, {'processing_options': changed})
            response = api.post(f'/api/v1/resource-versions/{version_id}/process')
            assert response.status_code == 400 and 'SOURCE_OCR_EXTERNAL_JOB_REQUIRES_PROVENANCE' in response.text
            with container.uow() as repo:
                observed = repo.get('source_file_imports', imports[0]['id'])
            assert observed['last_error_code'] == 'SOURCE_OCR_EXTERNAL_JOB_REQUIRES_PROVENANCE'
            assert source_snapshot() == before_source, 'UNPROVEN_SOURCE_HISTORY_MUTATED'
            record('existing_source_job_without_local_provenance_rejected_observably', response.status_code)
            changed = json.loads(json.dumps(options))
            changed['ocr_enabled'] = not changed.get('ocr_enabled', True)
            with container.uow() as repo:
                repo.update('resource_versions', version_id, {'processing_options': changed})
            response = api.post(f'/api/v1/resource-versions/{version_id}/process')
            assert response.status_code == 400 and 'SOURCE_OCR_PROCESSING_IDENTITY_CHANGED' in response.text
            response = api.get(f'/api/v1/resource-versions/{version_id}/ocr-pages/1', params={'job_id': job['id']})
            assert 400 <= response.status_code < 500
            record('changed_processing_fingerprint_process_and_raw_denied', True)
            with container.uow() as repo:
                repo.update('resource_versions', version_id, {'processing_options': options})
                repo.update('master_learning_resources', resource['id'], {'source_namespace': 'unauthorized:other-tenant'})
            response = api.post(f'/api/v1/resource-versions/{version_id}/process')
            assert 400 <= response.status_code < 500
            record('wrong_namespace_process_denied', response.status_code)
            assert source_snapshot() == before_source, 'SOURCE_ROWS_CHANGED_DURING_REJECTED_REQUESTS'
        finally:
            with container.uow() as repo:
                repo.update('resource_versions', version_id, {'processing_options': options})
                repo.update('master_learning_resources', resource['id'], {'source_namespace': resource['source_namespace']})
                repo.update('source_file_imports', imports[0]['id'], {key: imports[0][key] for key in
                    ('status', 'attempts', 'last_error_code', 'next_retry_at', 'updated_at')})
        record('fixture_mapping_and_policy_restored', True)
    finally:
        container.close()


def verify_postprocess_failure(settings, fixture, record):
    container = Container(settings)
    version_id = fixture['master_version_id']
    try:
        with container.uow() as repo:
            resource = repo.get('master_learning_resources', fixture['master_resource_id'])
            before = repo.find('content_units', {'resource_version_id': version_id}, order=('id',))
        assert resource['title'].startswith('CODEX E2E SOURCE OCR ') and resource['publication_status'] == 'DRAFT'
        database = container.documents.database
        units_before = list(database.mongo_content_units.find({'resource_version_id': version_id}).sort('_id'))
        assert before and units_before, 'EXISTING_OWNED_FIXTURE_REQUIRED'
        original = container.postprocess._save_unit
        attempted = []

        def interrupt(*args, **kwargs):
            assert args[1] == version_id, 'INJECTION_SCOPE_VIOLATION'
            unit_id = original(*args, **kwargs)
            attempted.append(unit_id)
            raise RuntimeError('controlled interruption before fixture postprocess SQL commit')

        container.postprocess._save_unit = interrupt
        try:
            container.postprocess.run(version_id)
        except DomainError as error:
            assert str(error) == 'Postprocess failed; existing content preserved'
        else:
            raise AssertionError('EXPECTED_POSTPROCESS_FAILURE')
        finally:
            container.postprocess._save_unit = original
        assert len(attempted) == 1
        with container.uow() as repo:
            assert repo.find('content_units', {'resource_version_id': version_id}, order=('id',)) == before
            job = repo.find('ocr_jobs', {'resource_version_id': version_id}, limit=1)[0]
            status = container.ocr_text_store.processing_state(repo, job)
        assert status == 'FINALIZATION_FAILED', 'FALSE_READY_AFTER_POSTPROCESS_FAILURE'
        assert list(database.mongo_content_units.find({'resource_version_id': version_id}).sort('_id')) == units_before
        record('real_postprocess_interruption_before_sql_commit_preserves_existing_sql_and_mongo', True)
        record('processing_state_after_failure', status)
    finally:
        container.close()


def verify_readback(api, settings, fixture, args, record):
    container = Container(settings)
    version_id = fixture['master_version_id']
    try:
        pipeline = container.ocr
        state = pipeline.state(version_id)
        source_version = state['identity']['source_version_id']
        source_job = state['source_job_id']
        with container.uow() as repo:
            resource = repo.get('master_learning_resources', fixture['master_resource_id'])
            job = repo.find('ocr_jobs', {'resource_version_id': version_id}, limit=1)[0]
        assert resource['title'].startswith('CODEX E2E SOURCE OCR '), 'TAGGED_FIXTURE_REQUIRED'
        assert resource['publication_status'] == 'DRAFT'

        def counts():
            with pipeline.source_engine.connect() as conn:
                jobs = conn.execute(text('SELECT count(*) FROM ocr_jobs WHERE resource_version_id=:v'),
                                    {'v': source_version}).scalar_one()
                tasks = conn.execute(text('SELECT count(*) FROM ocr_page_tasks WHERE job_id=:j'),
                                     {'j': source_job}).scalar_one()
            with container.uow() as repo:
                units = repo.count('content_units', {'resource_version_id': version_id})
            db = container.documents.database
            return dict(source_jobs=jobs, source_tasks=tasks, master_units=units,
                        mongo_pages=db.ocr_pages.count_documents({'job_id': source_job}),
                        mongo_units=db.mongo_content_units.count_documents({'resource_version_id': version_id}),
                        mongo_bags=db.content_bags.count_documents({'resource_version_id': version_id}))

        before = counts()
        observed_before = json.loads(args.metrics_file.read_text()) if args.metrics_file else None
        response = api.post(f'/api/v1/resource-versions/{version_id}/process')
        assert response.status_code == 200 and response.json()['job_id'] == job['id'], 'VERIFIED_REUSE_FAILED'
        response = api.post(f'/api/v1/resource-versions/{version_id}/ocr-jobs')
        assert response.status_code == 200 and response.json()['id'] == job['id']
        assert counts() == before, 'DUPLICATE_ARTIFACTS_ON_REUSE'
        record('reuse_counts_unchanged', before)
        if args.metrics_file:
            observed_after = json.loads(args.metrics_file.read_text())
            delta = {key: observed_after[key] - observed_before[key] for key in observed_before}
            assert all(value == 0 for value in delta.values()), 'REUSE_OPENED_FILE_OR_RECOGNIZED'
            record('reuse_observer_delta', delta)
        for page in range(1, job['total_pages'] + 1):
            response = api.get(f'/api/v1/resource-versions/{version_id}/ocr-pages/{page}', params={'job_id': job['id']})
            assert response.status_code == 200 and response.json()['available']
            assert 'no-store' in response.headers.get('cache-control', '')
        for endpoint in ['content-units', 'chunks']:
            response = api.get(f'/api/v1/resource-versions/{version_id}/{endpoint}')
            assert response.status_code == 200, 'CONTENT_HTTP_READ_FAILED'
        record('raw_units_chunks_http', True)
        bags = list(container.documents.database.content_bags.find({'resource_version_id': version_id}))
        assert bags, 'REAL_BAGS_MISSING'
        for bag in bags:
            assert len(bag['embedding']) == 1024 and any(bag['embedding']) and bag['lexical']
            assert bag['embedding_model'] == f'{container.indexing.embedding.model_id}@{container.indexing.embedding.version}'
        record('real_model_bags', {'count': len(bags), 'dimensions': 1024,
               'norm': math.sqrt(sum(value * value for value in bags[0]['embedding'])),
               'model': bags[0]['embedding_model']})
        for method in ['bow', 'vector', 'hybrid']:
            response = api.post('/api/v1/search', json={'query': 'photosynthesis', 'method': method,
                                'filters': {'resource_id': resource['id']}})
            assert response.status_code == 200, 'SEARCH_HTTP_FAILED'
            assert response.json()['total'] >= 1, 'FIXTURE_SEARCH_MISSING'
            record(f'search_{method}_http', {'status': response.status_code, 'total': response.json()['total']})
        no_match = api.post('/api/v1/search', json={'query': 'qxvzplmnonexistentterm', 'method': 'bow',
                            'filters': {'resource_id': resource['id']}})
        assert no_match.status_code == 200 and no_match.json()['total'] == 0
        record('nonmatching_lexical_query', True)
        anonymous = httpx.get(args.base_url + f'/api/v1/resource-versions/{version_id}/ocr-pages/1',
                              params={'job_id': job['id']}, headers=dict(api.headers), timeout=30)
        assert anonymous.status_code == 401, 'ANONYMOUS_RAW_ACCESS_ALLOWED'
        record('anonymous_raw_denied', anonymous.status_code)
        missing_csrf = api.build_request('POST', f'/api/v1/resource-versions/{version_id}/process')
        del missing_csrf.headers['X-CSRF-Token']
        assert api.send(missing_csrf).status_code == 403, 'MISSING_CSRF_ACCEPTED'
        wrong_origin = api.post('/api/v1/search', headers={'Origin': 'http://untrusted.invalid'},
                                json={'query': 'photosynthesis', 'method': 'bow'})
        assert wrong_origin.status_code == 403, 'FOREIGN_ORIGIN_ACCEPTED'
        foreign_job = api.get(f'/api/v1/resource-versions/{version_id}/ocr-pages/1', params={'job_id': source_job})
        assert foreign_job.status_code == 404, 'BROWSER_SOURCE_JOB_ID_ACCEPTED'
        record('csrf_origin_public_job_mapping_denied', True)
        if args.fixture_acl_failures:
            master_actor = state['identity']['actor_id'].removeprefix('master:')
            assert api.get('/api/v1/auth/me').json()['id'] == state['identity']['actor_id']
            with container.engine.connect() as conn:
                account = dict(conn.execute(text(
                    'SELECT id,is_active,role_code,source_origin FROM master_users WHERE id=:id'
                ), {'id': master_actor}).mappings().one())
            assert account['role_code'] == 'ADMIN' and account['source_origin'] == 'POSTGRESQL'
            account_backup = args.evidence.with_name('master-test-account-status-backup.json')
            account_backup.write_text(json.dumps(account), encoding='utf-8')
            try:
                with container.engine.begin() as conn:
                    conn.execute(text('UPDATE master_users SET is_active=false WHERE id=:id'), {'id': master_actor})
                inactive = api.get('/api/v1/auth/me')
                assert inactive.status_code == 401, 'INACTIVE_MASTER_ACCOUNT_ACCEPTED'
                record('inactive_master_test_account_denied', inactive.status_code)
            finally:
                with container.engine.begin() as conn:
                    conn.execute(text('UPDATE master_users SET is_active=:active WHERE id=:id'),
                                 {'id': master_actor, 'active': account['is_active']})
            with pipeline.source_engine.connect() as conn:
                source_resource = dict(conn.execute(text('SELECT * FROM learning_resources WHERE id=:id'),
                                       {'id': fixture['source_resource_id']}).mappings().one())
            assert source_resource['created_by'] == state['identity']['source_user_id']
            assert source_resource['title'].startswith('CODEX E2E SOURCE OCR ')
            try:
                with pipeline.source_engine.begin() as conn:
                    conn.execute(text('UPDATE learning_resources SET is_active=false WHERE id=:id'),
                                 {'id': source_resource['id']})
                response = api.get(f'/api/v1/resource-versions/{version_id}/ocr-pages/1', params={'job_id': job['id']})
                assert response.status_code in (403, 404), 'REVOKED_RAW_ACCESS_ALLOWED'
                response = api.post('/api/v1/search', json={'query': 'photosynthesis', 'method': 'bow',
                                    'filters': {'resource_id': resource['id']}})
                assert response.status_code == 200 and response.json()['total'] == 0, 'REVOKED_SEARCH_ACCESS_ALLOWED'
                record('source_revocation_without_master_sync', True)
            finally:
                with pipeline.source_engine.begin() as conn:
                    conn.execute(text('UPDATE learning_resources SET is_active=:active WHERE id=:id'),
                                 {'id': source_resource['id'], 'active': source_resource['is_active']})
            try:
                with pipeline.source_engine.begin() as conn:
                    conn.execute(text('UPDATE resource_versions SET source_hash=:hash WHERE id=:id'),
                                 {'id': source_version, 'hash': 'f' * 64})
                response = api.post(f'/api/v1/resource-versions/{version_id}/process')
                assert 400 <= response.status_code < 500, 'MUTATED_SOURCE_HASH_ACCEPTED'
                record('changed_source_hash_denied', response.status_code)
            finally:
                with pipeline.source_engine.begin() as conn:
                    conn.execute(text('UPDATE resource_versions SET source_hash=:hash WHERE id=:id'),
                                 {'id': source_version, 'hash': state['identity']['source_hash']})
    finally:
        container.close()


def verify_partial(api, settings, fixture, args, record):
    from bson import json_util

    assert args.metrics_file, 'REAL_OBSERVER_REQUIRED'
    container = Container(settings)
    pipeline = container.ocr
    version_id = fixture['master_version_id']
    document = None
    try:
        with container.uow() as repo:
            version = repo.get('resource_versions', version_id)
            resource = repo.get('master_learning_resources', version['resource_id'])
        assert resource['title'].startswith('CODEX E2E SOURCE OCR scan3 '), 'TAGGED_SCAN_FIXTURE_REQUIRED'
        state = pipeline.state(version_id)
        source_job = state['source_job_id']
        db = container.documents.database
        document = db.ocr_pages.find_one({'job_id': source_job, 'page_num': 2,
                                         'resource_version_id': fixture['source_version_id']})
        assert document is not None and document['source_checksum'] == state['identity']['source_hash']
        backup = args.evidence.with_name('partial-fixture-page-backup.json')
        backup.write_text(json_util.dumps(document), encoding='utf-8')
        other_pages = list(db.ocr_pages.find({'job_id': source_job, 'page_num': {'$ne': 2}}))
        before_units = sorted(row['_id'] for row in db.mongo_content_units.find({'resource_version_id': version_id}))
        before_bags = sorted(row['_id'] for row in db.content_bags.find({'resource_version_id': version_id}))
        observed_before = json.loads(args.metrics_file.read_text())
        deleted = db.ocr_pages.delete_one({'_id': document['_id'], 'job_id': source_job,
                                         'resource_version_id': fixture['source_version_id']})
        assert deleted.deleted_count == 1
        response = api.post(f'/api/v1/resource-versions/{version_id}/process')
        assert response.status_code in (200, 202) or (
            response.status_code in (400, 409)
            and response.json().get('detail', {}).get('code') == 'SOURCE_OCR_ALREADY_RUNNING')
        record('missing_page_process_http', response.status_code)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            with pipeline.source_engine.connect() as conn:
                job = dict(conn.execute(text('SELECT status,total_pages,completed_pages,failed_pages FROM ocr_jobs WHERE id=:id'),
                                        {'id': source_job}).mappings().one())
            saved = db.ocr_pages.find_one({'_id': document['_id']})
            if saved is not None and job['status'] == 'COMPLETED':
                break
            time.sleep(3)
        else:
            raise RuntimeError('PARTIAL_RECOVERY_TIMEOUT')
        assert saved['raw_text'] == document['raw_text'] and saved['source_checksum'] == document['source_checksum']
        assert list(db.ocr_pages.find({'job_id': source_job, 'page_num': {'$ne': 2}})) == other_pages
        assert sorted(row['_id'] for row in db.mongo_content_units.find({'resource_version_id': version_id})) == before_units
        assert sorted(row['_id'] for row in db.content_bags.find({'resource_version_id': version_id})) == before_bags
        observed_after = json.loads(args.metrics_file.read_text())
        delta = {key: observed_after[key] - observed_before[key] for key in observed_before}
        assert delta == {'file_open': 1, 'recognizer_calls': 1, 'engine_ocr_calls': 1}, 'UNEXPECTED_PARTIAL_OCR_WORK'
        record('partial_real_observer_delta', delta)
        record('partial_other_pages_units_bags_unchanged', True)
        record('partial_source_counts', job)
    finally:
        if document is not None and not container.documents.database.ocr_pages.find_one({'_id': document['_id']}):
            container.documents.insert_source_ocr_page(document)
        container.close()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        main()
    except Exception as error:
        candidate = str(error)
        code = getattr(error, 'code', None) or (candidate if candidate.isupper()
                and candidate.replace('_', '').isalnum() and len(candidate) <= 100 else 'inspect fixture stage evidence')
        print('SMOKE_FAILED', type(error).__name__, code, flush=True)
        raise SystemExit(1) from None
