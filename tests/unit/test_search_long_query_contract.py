import unicodedata

import pytest
from pydantic import ValidationError

from data_sync_etl.api.search_schemas import SearchRequest
from data_sync_etl.domain.search import bounded_query


def test_150_word_query_retains_negation_comparison_and_identifiers():
    query = ' '.join(['source'] * 140 + ['không', 'phải', 'SQL', 'NoSQL', 'C#', 'H2SO4', 'lớp', '6', 'not', '9'])
    assert len(query.split()) == 150
    assert SearchRequest(query=query).query == query
    assert bounded_query(unicodedata.normalize('NFD', query)) == query


@pytest.mark.parametrize('query', ['x' * 2001, 'word ' * 257, '', ' '])
def test_hard_limits_reject_instead_of_truncating_intent(query):
    with pytest.raises(ValidationError):
        SearchRequest(query=query)
    with pytest.raises(ValueError):
        bounded_query(query)
