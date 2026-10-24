import json
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from data_sync_etl.domain.concepts import (
    _OBSERVATIONS,
    Dictionary,
    ExpansionPolicy,
    concept_analysis_scope,
    default_dictionary,
)
from data_sync_etl.domain.lexical import bm25, lexical_document, query_terms, surface_tokens
from scripts.benchmark_multilingual_fixture import fixture_snapshot

FIXTURE = json.loads(Path('fixtures/search/multilingual_context.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('query,domain,expected,excluded', [
    ('base de données', None, 'cơ sở dữ liệu', None),
    ('database base de données', None, 'cơ sở dữ liệu', None),
    ('CSDL', 'computing', 'database', None),
    ('UCLN', 'mathematics', 'greatest common divisor', None),
    ('BCNN', 'mathematics', 'least common multiple', None),
    ('CO₂', None, 'khí cacbonic', None),
    ('cell excel', None, 'ô bảng tính', 'tế bào'),
    ('cell biology', None, 'tế bào', 'ô bảng tính'),
])
def test_fixture_only_translation_acronym_symbols_and_disambiguation(query, domain, expected, excluded):
    dictionary = Dictionary(fixture_snapshot(FIXTURE, research_approved=True))
    terms = {t.term for t in dictionary.expand(query, domain=domain).terms}
    assert expected in terms
    if excluded:
        assert excluded not in terms
    nfd = unicodedata.normalize('NFD', query)
    assert dictionary.expand(nfd, domain=domain).terms == dictionary.expand(query, domain=domain).terms


def test_draft_not_published_or_silently_enabled_and_ambiguous_term_retained():
    dictionary = Dictionary(fixture_snapshot(FIXTURE))
    assert not dictionary.expand('base de données UCLN', domain='mathematics').terms
    assert not any(cid.startswith('fixture:') for cid in default_dictionary().concepts)
    trial = Dictionary(fixture_snapshot(FIXTURE, research_approved=True))
    assert not trial.expand('cell').terms
    bounded = trial.expand('database photosynthesis', policy=ExpansionPolicy(max_total=2, max_per_concept=1))
    assert sum(t.relation != 'original' for t in bounded.terms) <= 2


def test_literal_identifiers_negation_and_phrases_remain_distinct():
    assert surface_tokens('C++ C# CO₂ CO2 HL26_XDRV_R012') == ['c++', 'c#', 'co₂', 'co2', 'hl26', 'xdrv', 'r012']
    terms = query_terms('không chưa chẳng not without except')
    assert {'không', 'chưa', 'chẳng', 'not', 'without', 'except'} <= set(terms)
    docs = {'cpp': lexical_document(['C++']), 'csharp': lexical_document(['C#'])}
    assert set(bm25('C++', docs)) == {'cpp'}
    assert set(bm25('C#', docs)) == {'csharp'}


def test_request_observations_are_bounded_policy_partitioned_and_not_shared(monkeypatch):
    dictionary = default_dictionary()
    original = dictionary._observations
    calls = []
    def observed(text, *, policy):
        calls.append(text)
        return original(text, policy=policy)
    monkeypatch.setattr(dictionary, '_observations', observed)
    with concept_analysis_scope():
        expected = list(dictionary.observations('ML model'))
        first = list(dictionary.observations('ML model'))
        assert first == expected and len(calls) == 1
        first.clear()
        assert list(dictionary.observations('ML model')) == expected
        list(dictionary.observations('ML model', policy=ExpansionPolicy(min_sense_score=99.)))
        assert len(calls) == 2
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(_OBSERVATIONS.get).result() is None
        list(dictionary.observations('x' * 1_000_001))
        assert len(_OBSERVATIONS.get()['entries']) == 2
    assert _OBSERVATIONS.get() is None
    list(dictionary.observations('ML model'))
    assert len(calls) == 4
    with pytest.raises(RuntimeError), concept_analysis_scope():
        raise RuntimeError('aborted request')
    assert _OBSERVATIONS.get() is None
