import pytest

from data_sync_etl.application.content_bags import ContentBagBuilder, StructuredUnit
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.mongo_documents import ContentUnitDocument


class Model:
    model_id, version, dimension = 'batch-test', '1', 2

    def __init__(self):
        self.calls = []

    def embed_many(self, texts):
        self.calls.append(list(texts))
        return [[0.25, 0.75] for _ in texts]

    def embed(self, text):
        pytest.fail('batch-capable model fell back to single-root inference')


def inputs(count=20, repeated=False):
    units, documents = [], []
    for index in range(count):
        key = f'leaf-{index}'
        units.append(StructuredUnit(id=key, resource_version_id='v1', parent_unit_id=None,
                                    unit_type='PAGE_TEXT', sequence_no=index + 1))
        documents.append(ContentUnitDocument(_id=key, resource_version_id='v1', created_at=now(),
            updated_at=now(), text='Plants need sunlight.', markdown='Plants need sunlight.',
            keywords=['plant' if repeated else f'plant-{index}'],
            projection_generation=1, is_current=True))
    return units, documents


def test_root_vectors_use_bounded_batches_and_keep_all_bag_identities():
    model = Model()
    units, docs = inputs()
    result = ContentBagBuilder(model).build('v1', units, docs, generation=1, content_fingerprint='a' * 64)
    assert [len(call) for call in model.calls] == [16, 4]
    assert len(result.bags) == 20
    assert {bag.id for bag in result.bags} == {unit.id for unit in units}
    assert all(bag.embedding == [0.25, 0.75] for bag in result.bags)


def test_duplicate_inputs_share_vector_only_inside_the_current_build():
    model = Model()
    units, docs = inputs(repeated=True)
    builder = ContentBagBuilder(model)
    for _ in range(2):
        result = builder.build('v1', units, docs, generation=1, content_fingerprint='a' * 64)
        assert len(result.bags) == 20
    assert model.calls == [['plant'], ['plant']]


def test_batch_cardinality_error_cannot_publish_partial_bags():
    model = Model()
    model.embed_many = lambda texts: [[0.25, 0.75]]
    units, docs = inputs()
    with pytest.raises(DomainError, match='Embedding batch size mismatch'):
        ContentBagBuilder(model).build('v1', units, docs, generation=1, content_fingerprint='a' * 64)
