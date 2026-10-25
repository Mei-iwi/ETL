"""Real SQLite/analyzer contracts. Dense doubles are not quality evidence."""

import unicodedata
from copy import deepcopy
from types import SimpleNamespace

import pytest

from data_sync_etl.api.search_schemas import SearchResponse
from data_sync_etl.application.metadata_search import MetadataSearchService, fuse_results
from data_sync_etl.application.search import SearchService
from data_sync_etl.domain.core import now
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.metadata_search import metadata_allowed, projection
from data_sync_etl.domain.search_access import SearchAccess


class Store:
    def __init__(self):
        self.rows = {}

    def ensure_ready(self):
        pass

    def get_many(self, keys):
        return deepcopy({key: row for key, row in self.rows.items() if key in keys and row['status'] == 'READY'})

    def upsert(self, row):
        self.rows[row['resource_version_id']] = deepcopy(row)

    def invalidate(self, key):
        if key in self.rows:
            self.rows[key]['status'] = 'STALE'

    def page(self, after, *, limit):
        return [deepcopy(row) for key, row in sorted(self.rows.items()) if key > after and row['status'] == 'READY'][:limit]


class Embedding:
    def __init__(self):
        self.calls, self.fail = [], False

    def model_identity(self):
        return {'model_id': 'BAAI/bge-m3', 'revision': 'contract-double', 'dimension': 1024}

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise IndexingError('SEMANTIC_MODEL_UNAVAILABLE')
        return [{'vector': [1.] + [0.] * 1023} for _ in texts]

    embed_queries = embed_documents


@pytest.fixture
def metadata(container):
    c = container
    c.sync.run(full=True)
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {
            'title': 'Cộng hai phân số khác mẫu số', 'mongo_id': 'desc', 'source_origin': 'LOCAL'})
        version = repo.insert('resource_versions', {
            'resource_id': 'resource-1', 'version_number': 1, 'lifecycle_status': 'INGESTED',
            'storage_bucket': 'fixture', 'storage_object_key': 'registered.mp4', 'source_hash': 'a' * 64})
    extension = SimpleNamespace(id='desc', description='Video hướng dẫn quy đồng mẫu số.', tags=['phân số', 'phép cộng'])
    service = MetadataSearchService(c.uow, SimpleNamespace(get_resource=lambda _: extension), Store(), Embedding(), c.settings)
    c.settings.search_metadata_enabled = True
    c.settings.search_metadata_content_enabled = False
    c.search = SearchService(c.indexing, c.settings, None, metadata=service)
    return c, version, extension, service


def search(c, query='quy đồng', method='bow', **kwargs):
    return c.search.search(query, method, access=kwargs.pop('access', SearchAccess('local_demo')), **kwargs)


@pytest.mark.parametrize('extension', ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv', 'png', 'jpg', 'jpeg', 'mp3', 'mp4'])
@pytest.mark.parametrize('method', ['bow', 'vector', 'hybrid'])
def test_registered_formats_without_content(metadata, extension, method, monkeypatch):
    c, version, _, service = metadata
    def forbidden(*args, **kwargs):
        pytest.fail('Metadata invoked source bytes or OCR')
    monkeypatch.setattr(c.source_files, 'open_stream', forbidden)
    monkeypatch.setattr(c.postprocess, 'run', forbidden)
    monkeypatch.setattr(c.process_version, 'run', forbidden)
    with c.uow() as repo:
        repo.update('resource_versions', version['id'], {'storage_object_key': f'registered.{extension}'})
    service.reconcile(apply=True, dense=method != 'bow')
    result = search(c, method=method)
    assert result['total'] == 1
    item = result['items'][0]
    assert item['resource_version_id'] == version['id'] and item['match_origin'] == 'METADATA'
    assert item['context_id'] is item['bag_id'] is item['content_unit_id'] is None
    assert not item['can_view_file']
    if method == 'bow':
        assert item['matched_fields'] == ['DESCRIPTION']
    SearchResponse.model_validate(result)
    with c.uow() as repo:
        assert not repo.find('content_units', {'resource_version_id': version['id']})
        assert repo.get('resource_versions', version['id'])['lifecycle_status'] == 'INGESTED'


def test_idempotent_updates_removal_without_events(metadata):
    c, _, extension, service = metadata
    assert service.reconcile(apply=True, dense=True)['embeds'] == 1
    calls = len(service.embedding.calls)
    assert service.reconcile(apply=True, dense=True)['unchanged'] == 1
    assert len(service.embedding.calls) == calls
    extension.description = 'Hình tam giác mới'
    assert search(c)['total'] == 0
    assert service.reconcile(apply=True, dense=True)['embeds'] == 1
    assert search(c, 'tam giác')['total'] == 1
    extension.description = None
    assert search(c, 'tam giác')['total'] == 0
    service.reconcile(apply=True)
    assert search(c, 'tam giác')['total'] == 0


@pytest.mark.parametrize('kind', ['unpublish', 'deleted', 'rejected', 'public', 'ungranted', 'source_missing'])
def test_access_denies_without_counts_or_snippets(metadata, kind):
    c, version, _, service = metadata
    service.reconcile(apply=True)
    access = SearchAccess('local_demo')
    with c.uow() as repo:
        if kind == 'unpublish':
            repo.update('master_learning_resources', 'resource-1', {'publication_status': 'UNPUBLISHED'})
        if kind == 'deleted':
            repo.update('master_learning_resources', 'resource-1', {'deleted_at': now()})
        if kind == 'rejected':
            repo.update('resource_versions', version['id'], {'lifecycle_status': 'REJECTED'})
        if kind == 'source_missing':
            repo.update('master_learning_resources', 'resource-1', {'source_missing_at': now()})
    if kind == 'public':
        access = SearchAccess()
    if kind == 'ungranted':
        access = SearchAccess('authenticated_user', owner_id='other')
    result = search(c, access=access)
    assert result['items'] == [] and result['total'] == 0


@pytest.mark.parametrize('race', ['description', 'revoke'])
def test_final_race_drops_stale(metadata, monkeypatch, race):
    c, _, extension, service = metadata
    service.reconcile(apply=True)
    validate = service.final_validate
    def changed(*args, **kwargs):
        if race == 'description':
            extension.description = None
        else:
            with c.uow() as repo:
                repo.update('master_learning_resources', 'resource-1', {'publication_status': 'UNPUBLISHED'})
        return validate(*args, **kwargs)
    monkeypatch.setattr(service, 'final_validate', changed)
    assert search(c)['total'] == 0


def test_build_race_and_retry(metadata, monkeypatch):
    _, _, extension, service = metadata
    original = service.embedding.embed_documents
    def changed(texts):
        extension.description = 'new metadata'
        return original(texts)
    monkeypatch.setattr(service.embedding, 'embed_documents', changed)
    assert service.reconcile(apply=True, dense=True)['stale'] == 1
    assert not service.store.rows
    assert service.reconcile(apply=True, dense=True)['ready'] == 1


def test_dense_failure_lexical_independent(metadata):
    c, _, _, service = metadata
    service.embedding.fail = True
    assert service.reconcile(apply=True, dense=True)['dense_failed'] == 1
    assert search(c)['total'] == 1
    result = search(c, method='vector')
    assert result['total'] == 0 and result['branch_status']['metadata_dense'] == 'NOT_READY'


def test_dense_corruption_mismatch_and_lexical_preserves_vector(metadata):
    c, version, _, service = metadata
    service.reconcile(apply=True, dense=True)
    row = service.store.rows[version['id']]
    row['model_identity'] = {'dimension': 384}
    assert search(c, method='vector')['total'] == 0
    row['model_identity'] = service.embedding.model_identity()
    row['vector'] = [0.] * 1024
    assert search(c, method='vector')['branch_status']['metadata_dense'] == 'INVALID_EMBEDDING'


@pytest.mark.parametrize('query,field', [('cộng hai phân số', 'TITLE'), ('quy đồng', 'DESCRIPTION'), ('phép cộng', 'TAGS')])
def test_field_evidence(metadata, query, field):
    c, _, _, service = metadata
    service.reconcile(apply=True)
    assert field in search(c, query)['items'][0]['matched_fields']


def test_empty_title_only_unicode_author_privacy(metadata):
    c, version, extension, service = metadata
    extension.description, extension.tags = None, []
    service.reconcile(apply=True)
    assert search(c, unicodedata.normalize('NFD', 'phân số'))['total'] == 1
    with c.uow() as repo:
        resource = repo.get('master_learning_resources', 'resource-1')
    resource['title'] = ''
    assert projection(resource, version, {}, extension) is None
    resource['title'] = 'safe'
    resource['owner_email'] = 'PRIVATE'
    extension.authors = [{'name': 'PRIVATE'}]
    assert 'PRIVATE' not in str(projection(resource, version, {}, extension))


@pytest.mark.parametrize('value', ['https://signed.example/token=123', 'C:\\private\\file', 'password=secret', 'x' * 8001], ids=['url', 'path', 'secret', 'length'])
def test_field_limits_and_secrets(metadata, value):
    _, _, extension, service = metadata
    extension.description = value
    with pytest.raises(IndexingError):
        service.reconcile(apply=True)
    assert not service.store.rows


def test_fusion_no_amplification_or_false_resource_merge():
    content = [{'resource_id': 'r', 'resource_version_id': 'v', 'score': 1., 'snippet': 'text'}]
    metadata = [{'resource_id': 'r', 'resource_version_id': 'v', 'score': 1., 'matched_fields': ['TITLE']}]
    result = fuse_results(content, metadata)
    assert len(result) == 1 and result[0]['match_origin'] == 'BOTH'
    assert fuse_results(content * 50, metadata) == result
    other = {**metadata[0], 'resource_id': 'other', 'resource_version_id': 'other-version'}
    assert len(fuse_results(content, [*metadata, other])) == 2


def test_latest_version_pagination(metadata):
    c, _, _, service = metadata
    with c.uow() as repo:
        version = repo.insert('resource_versions', {'resource_id': 'resource-1', 'version_number': 2,
            'lifecycle_status': 'INGESTED', 'storage_bucket': 'fixture', 'storage_object_key': 'new.mp3', 'source_hash': 'b' * 64})
    service.reconcile(apply=True)
    assert search(c)['items'][0]['resource_version_id'] == version['id']
    assert search(c, page=2, size=1)['items'] == []
    assert search(c, page=2, size=1)['total'] == 1


def test_owner_review_private_flags():
    resource = dict(id='r', created_by='owner', is_active=True, publication_status='PUBLISHED')
    version = dict(id='v', resource_id='r', lifecycle_status='INGESTED')
    access = SearchAccess('authenticated_user', frozenset({'r'}), owner_id='owner', allow_owned_unreviewed=True)
    assert metadata_allowed(resource, version, access)
    assert not metadata_allowed(resource, version, SearchAccess('authenticated_user', allow_public=True))
    assert not metadata_allowed({**resource, 'is_private': True}, version, access)
    assert not metadata_allowed(resource, {**version, 'review_status': 'QUARANTINED'}, access)


def test_feature_off_exact_delegate(metadata, monkeypatch):
    c, _, _, _ = metadata
    expected = {'items': [], 'total': 0, 'message': 'NO_READY_INDEX'}
    monkeypatch.setattr(c.search, '_content_search', lambda *args, **kwargs: expected)
    c.settings.search_metadata_enabled = False
    assert c.search.search('query', 'bow') is expected


def test_historical_version_never_inherits_new_description(metadata):
    c, old, _, service = metadata
    with c.uow() as repo:
        repo.insert('resource_versions', {'resource_id': 'resource-1', 'version_number': 2,
            'lifecycle_status': 'INGESTED', 'storage_bucket': 'test', 'storage_object_key': 'new.mp4', 'source_hash': 'b' * 64})
    service.reconcile(apply=True)
    assert search(c, filters={'resource_version_id': old['id']})['total'] == 0


def test_technical_symbols_and_no_answer(metadata):
    c, _, extension, service = metadata
    extension.description = 'Lập trình C++ và C# xử lý CO2; không dùng mật khẩu.'
    service.reconcile(apply=True)
    for query in ('C++', 'C#', 'CO2'):
        assert search(c, query)['items'][0]['matched_fields'] == ['DESCRIPTION']
    assert search(c, 'unobtanium quantum teleportation')['total'] == 0


def test_request_query_reuse_model_identity_and_no_global_cache(monkeypatch):
    from data_sync_etl.adapters.bge_m3 import BgeM3Embedding
    from data_sync_etl.ports.metadata_search import query_encoding_scope
    model = BgeM3Embedding(None, 'a' * 40)
    calls = []
    monkeypatch.setattr(model, 'prepare', lambda: None)
    def encode(texts):
        calls.append(list(texts))
        return [{'vector': [1.] + [0.] * 1023}]
    monkeypatch.setattr(model, '_encode', encode)
    with query_encoding_scope():
        vector = model.embed_queries(['safe'])[0]['vector']
        vector[0] = 9
        assert model.embed_queries(['safe'])[0]['vector'][0] == 1
        assert len(calls) == 1
        model.model_revision = 'b' * 40
        model.embed_queries(['safe'])
        assert len(calls) == 2
    model.embed_queries(['safe'])
    assert len(calls) == 3


def test_orphan_prune_dry_run_and_idempotent_tombstones(metadata):
    c, version, _, service = metadata
    service.reconcile(apply=True)
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'publication_status': 'UNPUBLISHED'})
    assert service.prune()['would_invalidate'] == 1
    assert service.store.rows[version['id']]['status'] == 'READY'
    assert service.prune(apply=True)['invalidated'] == 1
    assert service.store.rows[version['id']]['status'] == 'STALE'
    assert service.prune(apply=True)['invalidated'] == 0


def test_cli_dry_run_and_isolated_write_guard(metadata):
    from data_sync_etl.cli.main import dispatch, parser
    from data_sync_etl.domain.core import DomainError
    c, _, _, service = metadata
    c.metadata_search = service
    result = dispatch(c, parser().parse_args(['metadata-search-index', '--resource-id', 'resource-1']))
    assert result['status'] == 'DRY_RUN' and result['writes'] == result['embeds'] == 0
    service.store.database = SimpleNamespace(name='etl_metadata_test')
    with pytest.raises(DomainError, match='METADATA_APPLY_REQUIRES_ISOLATED_TEST_DATABASES'):
        dispatch(c, parser().parse_args(['metadata-search-index', '--apply']))
    assert not service.store.rows


def test_legacy_mongo_private_field_flags_cannot_be_projected_away():
    from data_sync_etl.adapters.mongo.foundation_store import MongoMetadataStore
    from data_sync_etl.domain.core import DomainError
    raw = {'_id': 'desc', 'created_at': now(), 'updated_at': now(), 'description': 'PRIVATE DESCRIPTION',
           'description_private': True}
    class Database:
        def __getitem__(self, _):
            return SimpleNamespace(find_one=lambda *args: deepcopy(raw))
    reader = MongoMetadataStore.__new__(MongoMetadataStore)
    reader.database = Database()
    with pytest.raises(DomainError, match='METADATA_FIELD_VISIBILITY_UNCONFIRMED'):
        reader.get_resource_search('desc')


def test_missing_optional_extension_still_indexes_authoritative_title(metadata):
    c, _, _, service = metadata
    service.documents.get_resource = lambda _: None
    assert service.reconcile(apply=True)['ready'] == 1
    item = search(c, 'cộng hai phân số')['items'][0]
    assert item['matched_fields'] == ['TITLE'] and item['context_id'] is None


def test_format_is_a_structured_filter_not_semantic_text(metadata):
    c, _, _, service = metadata
    service.reconcile(apply=True)
    assert search(c, filters={'resource_format': 'mp4'})['total'] == 1
    assert search(c, filters={'resource_format': 'mp3'})['total'] == 0
    assert all('mp4' not in row['text'] for row in service.store.rows.values())


def test_nonhit_revocation_cannot_preserve_old_metadata_idf(metadata, monkeypatch):
    c, _, _, service = metadata
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-2', {
            'source_origin': 'LOCAL', 'title': 'Unrelated background document'})
        repo.insert('resource_versions', {'resource_id': 'resource-2', 'version_number': 1,
            'lifecycle_status': 'INGESTED', 'storage_bucket': 'test', 'storage_object_key': 'other.mp3', 'source_hash': 'b' * 64})
    service.reconcile(apply=True)
    original = service.final_validate
    def changed(*args, **kwargs):
        with c.uow() as repo:
            repo.update('master_learning_resources', 'resource-2', {'publication_status': 'UNPUBLISHED'})
        return original(*args, **kwargs)
    monkeypatch.setattr(service, 'final_validate', changed)
    result = search(c)
    assert result['items'] == [] and result['total'] == 0
    assert result['branch_status']['metadata'] == 'STALE'
