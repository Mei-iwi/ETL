"""Observed corpus phrase candidates are bounded, opt-in, and not synonyms."""
import unicodedata

import pytest

from data_sync_etl.domain.lexical import bm25, lexical_document
from data_sync_etl.domain.query_variants import corpus_accent_variant


def test_new_corpus_candidate_policy_is_disabled_without_explicit_opt_in():
    from data_sync_etl.config import Settings

    assert Settings(_env_file=None).search_corpus_accent_candidates_enabled is False


@pytest.mark.parametrize('query', ['Cong hai phan so khac mau', 'Cộng hai phan so khac mau'])
def test_observed_phrase_finds_math_without_incidental_number_hit(query):
    docs = {'math': lexical_document(['Cộng hai phân số khác mẫu cần quy đồng.']),
            'security': lexical_document(['Xác thực hai yếu tố bảo mật.'])}
    assert corpus_accent_variant(query, docs) == 'cộng hai phân số khác mẫu'
    assert set(bm25(query, docs, accent_insensitive=True)) == {'math'}
    assert bm25(query, docs, accent_insensitive=True) == bm25(
        unicodedata.normalize('NFD', query), docs, accent_insensitive=True)


def test_single_word_and_colliding_phrases_never_select_first_accent_variant():
    docs = {'one': lexical_document(['mã định danh']), 'two': lexical_document(['má định danh'])}
    assert corpus_accent_variant('ma', docs) is None
    assert corpus_accent_variant('ma dinh danh', docs) is None
    assert corpus_accent_variant('mà dinh danh', docs) is None


def test_no_cross_leaf_phrase_no_identifier_rewrite_and_budget_abstains():
    docs = {'one': lexical_document(['mạng tinh', 'thể'])}
    assert corpus_accent_variant('mang tinh the', docs) != 'mạng tinh thể'
    assert corpus_accent_variant('HL26_mang_tinh_the', docs) is None
    assert corpus_accent_variant('mang tinh the', docs, max_tokens=1) is None
    clock = iter([0., 1.])
    assert corpus_accent_variant('mang tinh the', docs, clock=lambda: next(clock)) is None


def test_native_exact_score_is_not_replaced_by_weaker_accent_candidate():
    docs = {'one': lexical_document(['Mạng tinh thể'])}
    native = bm25('mạng tinh thể', docs)
    assert bm25('mạng tinh thể', docs, accent_insensitive=True) == native
    assert bm25('unknown astronomical query', docs, accent_insensitive=True) == {}


def test_one_large_passage_cannot_skip_the_time_budget():
    docs = {'one': lexical_document(['mạng tinh thể ' + 'filler ' * 600])}
    ticks = [0]
    def clock():
        ticks[0] += 1
        return 0. if ticks[0] <= 4 else 1.
    assert corpus_accent_variant('mang tinh the', docs, clock=clock) is None
    assert ticks[0] == 5
