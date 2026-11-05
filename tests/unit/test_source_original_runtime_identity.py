from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from data_sync_etl.api import routes
from data_sync_etl.domain.core import DomainError, SourceFileError
from data_sync_etl.domain.search_access import SearchAccess


@pytest.fixture
def source_file_request(monkeypatch):
    version = {'id': 'version', 'resource_id': 'resource', 'lifecycle_status': 'INGESTED'}
    resource = {'id': 'resource', 'source_origin': 'POSTGRESQL', 'publication_status': 'DRAFT', 'is_active': True}
    units = [{'id': 'unit', 'resource_version_id': 'version', 'review_status': 'APPROVED', 'retrieval_eligible': True}]
    state = {'identity': {'namespace': 'fixture:approved'}}
    calls = {'read_authority': 0, 'processing_identity': 0, 'read_bytes': 0}
    def processing_identity(_identity):
        calls['processing_identity'] += 1
        raise DomainError('SOURCE_OCR_PROCESSING_IDENTITY_CHANGED')
    def read_authority(*args, **kwargs):
        calls['read_authority'] += 1
        return True
    @contextmanager
    def stream(_reference):
        calls['read_bytes'] += 1
        yield BytesIO(b'%PDF-1.4 fixture original')
    class Repo:
        def get(self, table, key):
            return version if table == 'resource_versions' else resource
        def find(self, *args, **kwargs):
            return units
    @contextmanager
    def uow():
        yield Repo()
    resolver = SimpleNamespace(derived_index_authorized=read_authority,
        resolve=lambda _id: SimpleNamespace(filename='source.pdf', mime='application/pdf'),
        require_content_capabilities=lambda _reference: None, open_verified_stream=stream)
    container = SimpleNamespace(uow=uow, settings=SimpleNamespace(index_max_units=100), source_resolver=resolver,
        ocr=SimpleNamespace(source_owned=True, state=lambda _id: state, authorize_identity=processing_identity))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(container=container)),
                              state=SimpleNamespace(auth_principal=SimpleNamespace(role='ADMIN')), headers={})
    monkeypatch.setattr(routes, 'search_access', lambda _request: SearchAccess('authenticated_admin', actor_id='master:fixture'))
    monkeypatch.setattr(routes, 'services', lambda _request: container)
    return request, container, resource, units, calls


def test_original_file_uses_read_authority_not_current_ocr_library_revision(source_file_request):
    request, _, _, _, calls = source_file_request
    response = routes.resource_file('version', request)
    assert response.status_code == 200 and response.body.startswith(b'%PDF')
    assert calls['processing_identity'] == 0
    assert calls['read_authority'] >= 3  # Before IO, inside the stream gate, after IO.
    assert calls['read_bytes'] == 1
    with pytest.raises(DomainError, match='SOURCE_OCR_PROCESSING_IDENTITY_CHANGED'):
        routes.bilingual_access(request, 'version')  # OCR pages/translation still fail closed.


@pytest.mark.parametrize('denial', ['capability', 'unit', 'inactive', 'source_hash'])
def test_original_file_does_not_bypass_current_read_or_hash_guards(source_file_request, denial):
    request, container, resource, units, calls = source_file_request
    if denial == 'capability':
        container.source_resolver.derived_index_authorized = lambda *args, **kwargs: False
    elif denial == 'unit':
        units[0]['review_status'] = 'REJECTED'
    elif denial == 'inactive':
        resource['is_active'] = False
    else:
        @contextmanager
        def changed(_reference):
            raise SourceFileError('SOURCE_HASH_MISMATCH')
            yield  # pragma: no cover
        container.source_resolver.open_verified_stream = changed
        response = routes.resource_file('version', request)
        assert response.status_code == 409 and calls['read_bytes'] == 0
        return
    with pytest.raises(HTTPException) as caught:
        routes.resource_file('version', request)
    assert caught.value.status_code in (403, 404)
    assert calls['read_bytes'] == 0
