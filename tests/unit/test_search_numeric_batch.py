import math
import random

import pytest

from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.search import cosine, cosine_many


def test_batch_cosine_matches_reference_including_extreme_finite_values():
    rng = random.Random(20261024)
    for dimension in (3, 384, 1024):
        query = [rng.uniform(-1, 1) for _ in range(dimension)]
        candidates = [[rng.uniform(-1, 1) * scale for _ in range(dimension)]
                      for scale in (1e-308, 1., 1e308)]
        assert cosine_many(query, candidates) == pytest.approx(
            [cosine(query, vector) for vector in candidates], abs=2e-14)
    assert cosine_many([1., 0.], [[1., 0.], [-1., 0.], [0., 1.]]) == [1., -1., 0.]


@pytest.mark.parametrize('bad', [[0., 0.], [True, 1.], [math.inf, 1.], [math.nan, 1.], [1.]])
def test_batch_cosine_preserves_invalid_vector_rejection(bad):
    with pytest.raises(IndexingError):
        cosine_many([1., 0.], [bad])
    with pytest.raises(IndexingError):
        cosine_many(bad, [[1., 0.]])
