import math

import pytest

from data_sync_etl.domain.indexing import IndexingError, validate_vector
from data_sync_etl.domain.search import cosine, cosine_many


@pytest.mark.parametrize('vector', [[0., -0., 1e308, -1e-308], (0., -0., 1e308, -1e-308)])
def test_float_vectors_keep_exact_values_and_return_independent_list(vector):
    result = validate_vector(vector, 4)
    assert result == list(vector)
    assert result is not vector
    assert math.copysign(1, result[1]) == -1
    result[0] = 12.
    assert vector[0] == 0.


@pytest.mark.parametrize('vector,dimension', [
    ([float('nan'), 1.], 2), ([float('inf'), 1.], 2), ([-float('inf'), 1.], 2),
    ([0., -0.], 2), ([True, 1.], 2), ([False, 1.], 2), (['1', 2.], 2),
    ([1., 2.], 3), ([], 0), ([None, 1.], 2),
])
def test_invalid_inputs_still_fail_closed(vector, dimension):
    with pytest.raises(IndexingError, match='INVALID_EMBEDDING'):
        validate_vector(vector, dimension)


def test_mixed_numeric_and_subclass_inputs_keep_existing_conversion_and_cosine():
    class FloatSubclass(float):
        pass
    assert validate_vector([1, FloatSubclass(2.)], 2) == [1., 2.]
    query = [1e308, 1e308, 0., -1e308]
    vectors = [[1e308, -1e308, 1e-308, 1e308], [0., 1., 0., -1.]]
    assert cosine_many(query, vectors) == pytest.approx([cosine(query, vector) for vector in vectors], abs=1e-15)
