from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.domain.indexing import IndexingError


def runtime(tmp_path):
    model = LocalSentenceEmbedding(tmp_path, 'fixture-only', 'v1', 3)
    model.prepare = Mock()
    model._model = Mock()
    return model


def test_batch_embedding_preserves_order_and_vector_contract(tmp_path):
    model = runtime(tmp_path)
    model._model.encode.return_value = SimpleNamespace(tolist=lambda: [[1., 0., 0.], [0., 1., 0.]])
    assert model.embed_many(['Học máy', 'binary search']) == [[1., 0., 0.], [0., 1., 0.]]
    model.prepare.assert_called_once()
    model._model.encode.assert_called_once_with(['Học máy', 'binary search'], batch_size=16, show_progress_bar=False)


@pytest.mark.parametrize('texts', [[], 'not-an-array', [None], ['a'] * 17])
def test_invalid_batch_is_rejected_before_loading_model(tmp_path, texts):
    model = runtime(tmp_path)
    with pytest.raises(IndexingError, match='INVALID_EMBEDDING'):
        model.embed_many(texts)
    model.prepare.assert_not_called()


def test_inference_failure_does_not_expose_provider_details(tmp_path):
    model = runtime(tmp_path)
    model._checked = True
    model._model.encode.side_effect = RuntimeError('PRIVATE_PROVIDER_DETAILS')
    with pytest.raises(IndexingError, match='BLOCKED_EMBEDDING_RUNTIME') as error:
        model.embed_many(['source expression'])
    assert 'PRIVATE_PROVIDER_DETAILS' not in str(error.value)
    assert model._checked is False


def test_wrong_batch_cardinality_is_not_silently_zipped(tmp_path):
    model = runtime(tmp_path)
    model._model.encode.return_value = SimpleNamespace(tolist=lambda: [[1., 0., 0.]])
    with pytest.raises(IndexingError, match='INVALID_EMBEDDING'):
        model.embed_many(['a', 'b'])
