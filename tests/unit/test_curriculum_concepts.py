import pytest

from data_sync_etl.domain.concepts import default_dictionary
from data_sync_etl.domain.lexical import bm25, lexical_document


@pytest.mark.parametrize('query,expected', [
    ('How to add fractions with different denominators?', 'fractions'),
    ('photosynthesis', 'plants'),
    ('directly proportional', 'proportion'),
])
def test_curriculum_translation_retrieves_source_language_without_an_encoder(query, expected):
    documents = {
        'fractions': lexical_document(['Cộng hai phân số khác mẫu số bằng cách quy đồng.']),
        'plants': lexical_document(['Quang hợp ở thực vật cần ánh sáng.']),
        'proportion': lexical_document(['Hai đại lượng tỉ lệ thuận có hệ số không đổi.']),
        'unrelated': lexical_document(['Cách sử dụng mật khẩu an toàn.']),
    }
    ranking = bm25(query, documents)
    assert max(ranking, key=ranking.get) == expected
    assert 'unrelated' not in ranking


def test_curriculum_concepts_do_not_equate_opposite_operations_or_greenness_with_photosynthesis():
    dictionary = default_dictionary()
    for query, forbidden in [('subtract fractions', 'etl:fraction_addition'),
                             ('inverse proportion', 'etl:direct_proportion'),
                             ('green leaf', 'etl:photosynthesis')]:
        expanded = dictionary.expand(query)
        assert forbidden not in {term.concept_id for term in expanded.terms}


def test_coverage_rejects_incidental_generic_word_but_keeps_a_translated_concept():
    documents = {'generic': lexical_document(['time schedule tomorrow']),
                 'translation': lexical_document(['Cộng hai phân số khác mẫu số.'])}
    unrelated = 'quantum gravity wormhole time spacetime black holes'
    assert bm25(unrelated, documents)
    assert not bm25(unrelated, documents, min_coverage=.15)
    assert 'translation' in bm25('add fractions with different denominators', documents, min_coverage=.15)
