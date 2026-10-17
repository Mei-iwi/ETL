"""Synthetic hierarchy and model doubles ONLY for isolated tests, never production."""

from copy import deepcopy

from data_sync_etl.application.indexing import IndexingService
from data_sync_etl.domain.indexing import IndexingError, digest
from data_sync_etl.domain.mongo_documents import ContentUnitDocument


class ModelDouble:
    model_id, version, dimension = 'test-double-only', 'v1', 3
    enabled = True

    def available(self):
        return self.enabled

    def prepare(self):
        if not self.enabled:
            raise IndexingError('BLOCKED_EMBEDDING_RUNTIME', 503)

    def embed(self, text):
        return [0.1, 0.2, 0.3]


class MemoryReader:
    def __init__(self, documents):
        self.documents = documents

    def units(self, version_id, *, limit=100, offset=0):
        docs = [ContentUnitDocument.model_validate(d) for d in self.documents.units.values()
                if d['resource_version_id'] == version_id and d.get('is_current')]
        return sorted(docs, key=lambda d: d.id)[offset:offset + limit]

    def get_unit(self, unit_id):
        return ContentUnitDocument.model_validate(deepcopy(self.documents.units[unit_id]))

    def update_leaf_keywords(self, observed, keywords):
        current = self.get_unit(observed.id)
        assert current.text == observed.text and current.projection_generation == observed.projection_generation
        self.documents.units[observed.id]['keywords'] = keywords

    def content_fingerprint(self, version_id, *, generation=None):
        docs = self.units(version_id, limit=10000)
        assert all(d.projection_generation == generation for d in docs)
        return digest([(d.id, d.text, d.keywords, d.projection_generation) for d in docs])


class MemoryBags:
    dimension = 3

    def __init__(self, reader):
        self.reader, self.rows = reader, {}

    def ensure_ready(self):
        pass

    def invalidate_other_roots(self, version_id, root_ids, generation):
        for key, doc in self.rows.items():
            if doc.resource_version_id == version_id and key not in root_ids and doc.projection_generation <= generation:
                doc.is_current = False

    def upsert_bag(self, document):
        assert document.content_fingerprint == self.reader.content_fingerprint(document.resource_version_id,
                                                                    generation=document.projection_generation)
        self.rows[document.id] = deepcopy(document)

    def get_bag(self, root_id):
        doc = self.rows.get(root_id)
        return deepcopy(doc) if doc and doc.is_current else None


def setup_index(c):
    reader = MemoryReader(c.documents)
    c.settings.index_embedding_dimension = 3
    c.settings.admin_enabled = True
    c.settings.search_internal_dev_enabled = True
    c.indexing = IndexingService(c.uow, reader, ModelDouble(), c.settings, bags=MemoryBags(reader))
    c.search.indexing = c.indexing
    c.search.bags = c.indexing.bags
    c.processing_finalizer.on_content_ready = c.indexing.enqueue_auto
    return c.indexing


def seed_structure(c, version_id):
    """Explicit synthetic upstream hierarchy with real PG IDs and FK-consistent text rows."""
    with c.uow() as repo:
        leaves = repo.find('content_units', {'resource_version_id': version_id}, order=('sequence_no',))
        if any(u['unit_type'] == 'CHAPTER' for u in leaves):
            return
        body = repo.get('content_unit_texts', leaves[0]['id'])
        parent = None
        for sequence, kind in enumerate(('CHAPTER', 'LESSON', 'SECTION'), len(leaves) + 1):
            root = repo.insert('content_units', dict(resource_version_id=version_id, parent_unit_id=parent,
                unit_type=kind, title='Synthetic test ' + kind, sequence_no=sequence,
                review_status='UNREVIEWED', retrieval_eligible=False))
            repo.insert('content_unit_texts', dict(content_unit_id=root['id'],
                page_result_id=body['page_result_id'], text=''))
            parent = root['id']
        for leaf in leaves:
            repo.update('content_units', leaf['id'], dict(parent_unit_id=parent))


def structured_projection(c):
    """Test fixture supplies hierarchy after postprocess, before the real projection."""
    original = c.postprocess.run_locked

    def run(version_id, **kwargs):
        result = original(version_id, **kwargs)
        seed_structure(c, version_id)
        return result

    c.postprocess.run_locked = run
