import json
from copy import deepcopy

import pytest

from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.source_ocr_contract import SourceOCRContract
from tests.unit.test_source_ocr_inventory import audit, result
from tests.unit.test_source_ocr_runtime import runtime as runtime


def enable_proposed_scope(r):
    value = json.loads(r.container.settings.source_ocr_contract_file.read_text())
    value['allowed_version_ids'] = []
    value['namespace_scope'] = {'service_actor_id': 'master:a1', 'service_role': 'ADMIN',
        'version_lifecycles': ['APPROVED'], 'resource_publication_statuses': ['DRAFT', 'PUBLISHED']}
    policy = SourceOCRContract.from_owner_contract(value, expected_namespace='source-a')
    r.pipeline.contract = policy
    r.pipeline.writer.contract = policy
    return value


def test_imported_version_requires_no_upload_receipt_or_version_allowlist(runtime):
    r = runtime
    enable_proposed_scope(r)
    r.uow.tables['master_learning_resources']['r1']['created_by'] = 'another-author'
    assert not r.pipeline.contract.version_ids
    assert not r.pipeline.authorize_created_version({'namespace': 'source-a'})
    assert audit(r) == ['mv1']
    job = r.pipeline.create_job('mv1', access=r.access)
    assert r.container.worker.once('namespace-test')
    assert r.uow.get('ocr_jobs', job['id'])['status'] == 'COMPLETED'
    assert r.uow.get('master_learning_resources', 'r1')['publication_status'] == 'DRAFT'
    before = deepcopy((r.source.jobs, r.source.tasks, r.content.pages))
    r.pipeline.create_job('mv1', access=r.access)
    assert (r.source.jobs, r.source.tasks, r.content.pages) == before


@pytest.mark.parametrize('change', ['hash', 'namespace', 'deleted', 'inactive', 'lifecycle', 'actor'])
def test_namespace_policy_keeps_all_authority_guards(runtime, change):
    r = runtime
    enable_proposed_scope(r)
    identity = {'namespace': 'source-a', 'master_version_id': 'mv1', 'source_version_id': 'v1',
                'source_hash': r.source.versions['v1']['source_hash'], 'source_user_id': 'u1',
                'actor_id': 'master:a1', 'role': 'ADMIN'}
    if change == 'hash':
        identity['source_hash'] = 'f' * 64
    if change == 'namespace':
        identity['namespace'] = 'other-tenant'
    if change == 'deleted':
        r.uow.tables['master_learning_resources']['r1']['deleted_at'] = 'revoked'
    if change == 'inactive':
        r.uow.tables['master_learning_resources']['r1']['is_active'] = False
    if change == 'lifecycle':
        r.source.versions['v1']['lifecycle_status'] = 'PENDING_PRE_REVIEW'
    if change == 'actor':
        identity['actor_id'] = 'master:viewer'
    assert not r.pipeline.authorize_namespace_version(identity)
    assert not r.opens and not r.source.jobs


def test_namespace_does_not_adopt_existing_unknown_source_job(runtime):
    r = runtime
    enable_proposed_scope(r)
    r.pipeline.create_job('mv1', access=r.access)
    source_before = deepcopy((r.source.jobs, r.source.tasks))
    r.uow.tables['resource_versions']['mv1']['processing_options'] = {}
    assert audit(r) == []
    assert result(r)['external_status'] == 'UNTRUSTED'
    assert (r.source.jobs, r.source.tasks) == source_before


def test_future_import_uses_same_policy_and_does_not_scan_other_versions(runtime, monkeypatch):
    r = runtime
    enable_proposed_scope(r)
    # This mapping arrives after policy approval, without editing that policy.
    r.source.versions['future-v'] = deepcopy(r.source.versions['v1'])
    r.uow.tables['resource_versions']['future-mv'] = dict(
        deepcopy(r.uow.tables['resource_versions']['mv1']), id='future-mv', version_number=2)
    r.uow.tables['source_file_imports']['future-i'] = dict(
        deepcopy(r.uow.tables['source_file_imports']['i1']), id='future-i',
        target_version_id='future-mv', source_version_id='future-v', source_version_number=2)
    adapter = r.pipeline.resolver._source_adapter('SRC_TEST')
    get_versions = adapter.get_versions
    monkeypatch.setattr(adapter, 'get_versions', lambda ids: {
        key: {**value, 'source_version_number': 2 if key == 'future-v' else 1}
        for key, value in get_versions(ids).items()})
    source = adapter.get_versions(['future-v'])['future-v']
    monkeypatch.setattr(r.pipeline, 'states', lambda: pytest.fail('scanned unrelated imports'))
    assert r.pipeline.status_allowed(source, 'source-a')
    r.uow.tables['source_file_imports']['ambiguous-i'] = dict(
        deepcopy(r.uow.tables['source_file_imports']['future-i']), id='ambiguous-i')
    assert not r.pipeline.status_allowed(source, 'source-a')
    assert not r.opens and not r.source.jobs


@pytest.mark.parametrize('change', [
    {'service_actor_id': 'unapproved'}, {'service_role': 'VIEWER'},
    {'resource_publication_statuses': ['ARCHIVED']}, {'version_lifecycles': ['REJECTED']},
    {'service_actor_id': []}, {'allow_everyone': True},
])
def test_scope_requires_exact_owner_policy_dimensions(runtime, change):
    value = enable_proposed_scope(runtime)
    value['namespace_scope'].update(change)
    with pytest.raises(DomainError):
        SourceOCRContract.from_owner_contract(value, expected_namespace='source-a')
