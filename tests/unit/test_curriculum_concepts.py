"""Cross-language recall requires a reviewed corpus edge; raw BoW stays literal."""
import pytest

from data_sync_etl.domain.concepts import empty_dictionary
from data_sync_etl.domain.lexical import bm25, lexical_document
from tests.corpus_dictionary_support import reviewed_dictionary


@pytest.mark.parametrize('query', ['zircon lattice', 'réseau cristallin'])
def test_corpus_relation_retrieves_source_language_only_after_review(tmp_path, query):
    docs = {'relevant': lexical_document(['Mạng tinh thể.']),
            'unrelated': lexical_document(['Mật khẩu an toàn.'])}
    assert bm25(query, docs) == {}
    with reviewed_dictionary(tmp_path,
            'vi: mạng tinh thể | en: zircon lattice | fr: réseau cristallin', query) as dictionary:
        scores = bm25(query, docs, dictionary=dictionary)
        assert set(scores) == {'relevant'}
        assert all(term.sources for term in dictionary.expand(query).terms)


def test_no_seed_pair_or_unknown_acronym_is_implicitly_approved():
    for query in ('photosynthesis', 'green leaf', 'BCNN', 'ML', 'QH'):
        assert not empty_dictionary().expand(query).terms


def test_literal_coverage_retains_real_matches_and_rejects_incidental_word():
    docs = {'generic': lexical_document(['time schedule tomorrow']),
            'specific': lexical_document(['zircon lattice spectral properties'])}
    query = 'quantum gravity wormhole time spacetime black holes'
    assert bm25(query, docs)
    assert not bm25(query, docs, min_coverage=.2)
    assert 'specific' in bm25('zircon lattice', docs, min_coverage=.5)
