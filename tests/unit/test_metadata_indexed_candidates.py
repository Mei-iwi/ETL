"""Exact synthetic lexical ground truth; memory index is NOT physical DB evidence."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import insert

from data_sync_etl.db.models import MasterLearningResource, ResourceVersion
from data_sync_etl.domain.core import now
from data_sync_etl.domain.lexical import ANALYZER_VERSION, bm25
from data_sync_etl.domain.metadata_search import SEARCH_FIELDS, lexical_fields
from data_sync_etl.domain.search import rrf
from data_sync_etl.domain.search_access import SearchAccess
from tests.unit.test_metadata_search import Store, search
from tests.unit.test_metadata_search import metadata as metadata


class IndexedStore(Store):
    def __init__(self):
        super().__init__()
        self.candidate_reads = []

    def ensure_candidates_ready(self):
        pass

    def get_headers(self, keys):
        fields = ('resource_version_id', 'status', 'template', 'generation', 'projection_fingerprint',
                  'lexical_fingerprint', 'dense_input_fingerprint', 'candidate_terms', 'candidate_analyzer',
                  'dense_status', 'model_identity')
        assert len(keys) <= 250
        return {key: deepcopy({name: self.rows[key].get(name) for name in fields}) for key in keys if key in self.rows}

    def candidates(self, terms, version_ids, *, limit):
        self.candidate_reads.append((list(terms), list(version_ids)))
        keys = [key for key in sorted(version_ids) if set(self.rows[key].get('candidate_terms', ())) & set(terms)]
        if len(keys) > limit:
            from data_sync_etl.domain.indexing import IndexingError
            raise IndexingError('METADATA_CANDIDATE_LIMIT', 422)
        return keys


def enable(c, service):
    c.settings.search_metadata_indexed_candidates_enabled = True
    service.store = IndexedStore()
    return service.store


def test_indexed_vietnamese_statistics_equal_stored_lexical_representation(metadata):
    c, _, _, service = metadata
    enable(c, service)
    service.reconcile(apply=True)
    snapshots = service.indexed_snapshots({}, SearchAccess('local_demo'))
    for field in SEARCH_FIELDS:
        documents = {key: lexical_fields(snap)[field] for key, (_, snap) in snapshots.items()}
        assert snapshots.lengths[field] == sum(doc.length for doc in documents.values())
        for term in {term for doc in documents.values() for term in doc.terms}:
            assert snapshots.document_frequencies[field][term] == sum(term in doc.terms for doc in documents.values())
    assert search(c, 'phân số')['total'] == 1


def seed(c, count):
    with c.uow() as repo:
        template = repo.get('master_learning_resources', 'resource-1')
        repo.update('master_learning_resources', 'resource-1', {'title': 'Late sentinel title'})
        resources, versions = [], []
        for i in range(count - 1):
            key = f'indexed-scale-{i:05d}'
            resources.append({**template, 'id': key, 'mongo_id': None,
                              'title': 'Exact needle tensor' if i == count - 2 else 'Bài giảng thông thường'})
            versions.append(dict(id=f'indexed-version-{i:05d}', resource_id=key, version_number=1,
                created_at=now(), updated_at=now(), lifecycle_status='INGESTED',
                storage_bucket='synthetic', storage_object_key='registered.mp4', source_hash='b' * 64))
        repo.session.execute(insert(MasterLearningResource), resources)
        repo.session.execute(insert(ResourceVersion), versions)


def index_pages(c, service):
    """Simulate the existing bounded worker, not an unbounded reconcile apply."""
    after = None
    while True:
        snapshots, cursor, count = service.snapshots({'_after_resource_id': after}, SearchAccess('authenticated_admin'), _page=True)
        for _version_id, (_, snap) in snapshots.items():
            from data_sync_etl.domain.metadata_search import candidate_terms
            service.store.upsert({**snap, 'status': 'READY', 'updated_at': now(),
                'lexical': {name: doc.model_dump() for name, doc in lexical_fields(snap).items()},
                'vector': None, 'model_identity': None, 'dense_status': 'NOT_INDEXED',
                'candidate_analyzer': ANALYZER_VERSION, 'candidate_terms': candidate_terms(snap['fields'])})
        if count < 250:
            break
        after = cursor


@pytest.mark.parametrize('count', [201, 500, 2000])
def test_indexed_retrieval_beyond_200_has_exact_full_scope_bm25_parity(metadata, count):
    c, _, _, service = metadata
    store = enable(c, service)
    seed(c, count)
    index_pages(c, service)
    items, snapshots, status, branches = service.retrieve('needle', 'bow', {}, SearchAccess('local_demo'),
        dictionary=c.search.dictionary, policy=c.settings.search_expansion)
    assert len(snapshots) == count and status['lexical'] == 'READY'
    assert [row['resource_id'] for row in items] == [f'indexed-scale-{count-2:05d}']
    full = {key: lexical_fields(snap) for key, (_, snap) in snapshots.items()}
    truth = rrf(*(bm25('needle', {key: fields[field] for key, fields in full.items()},
        dictionary=c.search.dictionary, policy=c.settings.search_expansion) for field in SEARCH_FIELDS))
    assert branches['metadata_lexical'] == truth
    assert len(store.candidate_reads) == 1
    result = search(c, 'needle', size=1)
    assert result['total'] == 1 and result['total_pages'] == 1
    late = search(c, 'sentinel')
    assert late['total'] == 1 and late['items'][0]['resource_id'] == 'resource-1'
    assert search(c, 'needle', page=2, size=1)['items'] == []
    assert search(c, 'nonexistent xyzzy')['total'] == 0
    assert not service.embedding.calls  # Lexical mode did not load/encode dense.
    vector = search(c, 'needle', method='vector')
    assert vector['total'] == 0 and vector['branch_status']['metadata_dense'] == 'BLOCKED_SCALE'


def test_missing_candidate_representation_requires_explicit_reconcile(metadata):
    c, version, _, service = metadata
    service.reconcile(apply=True)
    previous = deepcopy(service.store.rows)
    enable(c, service).rows = previous
    assert search(c)['branch_status']['metadata_lexical'] == 'NOT_READY'
    assert service.reconcile(apply=True)['writes'] == 1
    assert search(c)['total'] == 1
    assert service.store.rows[version['id']]['candidate_analyzer'] == ANALYZER_VERSION


def test_scope_private_documents_cannot_affect_candidate_cap_or_rank(metadata):
    c, _, _, service = metadata
    store = enable(c, service)
    seed(c, 201)
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'created_by': 'owner'})
    index_pages(c, service)
    access = SearchAccess('authenticated_user', resource_ids=frozenset({'resource-1'}),
        owner_id='owner', allow_owned_unreviewed=True)
    result = search(c, 'quy đồng', access=access)
    assert result['total'] == 1
    assert len(store.candidate_reads[-1][1]) == 1
    assert result['items'][0]['resource_id'] == 'resource-1'


def test_nonhit_revocation_changes_full_authority_and_discards_old_ranks(metadata, monkeypatch):
    c, _, _, service = metadata
    store = enable(c, service)
    seed(c, 201)
    index_pages(c, service)
    original = store.candidates
    def race(*args, **kwargs):
        keys = original(*args, **kwargs)
        with c.uow() as repo:
            repo.update('master_learning_resources', 'indexed-scale-00000', {'publication_status': 'UNPUBLISHED'})
        return keys
    monkeypatch.setattr(store, 'candidates', race)
    result = search(c, 'needle')
    assert result['total'] == 0 and result['branch_status']['metadata'] == 'STALE'


@pytest.mark.parametrize('query', ['quy đồng', 'phân số', 'phép cộng', '  quy đồng  '])
def test_approved_expansion_candidates_preserve_complete_scoped_ranks(metadata, query):
    c, _, _, service = metadata
    service.reconcile(apply=True)
    baseline = search(c, query)
    previous = deepcopy(service.store.rows)
    enable(c, service).rows = previous
    service.reconcile(apply=True)
    after = search(c, query)
    assert after['items'] == baseline['items']


def test_corrupt_candidate_cannot_influence_other_ranks(metadata):
    c, version, _, service = metadata
    enable(c, service)
    service.reconcile(apply=True)
    service.store.rows[version['id']]['lexical']['description']['length'] += 1
    result = search(c)
    assert result['total'] == 0 and result['branch_status']['metadata'] == 'METADATA_INDEX_STALE'


def test_content_cap_does_not_destroy_indexed_metadata_results(metadata):
    c, _, _, service = metadata
    enable(c, service)
    seed(c, 201)
    index_pages(c, service)
    c.search.bags = SimpleNamespace(ensure_ready=lambda: None)
    c.settings.search_metadata_content_enabled = True
    c.settings.search_max_bags = 100
    result = search(c, 'sentinel')
    assert result['total'] == 1 and result['branch_status']['content'] == 'SEARCH_LIMIT_EXCEEDED'
