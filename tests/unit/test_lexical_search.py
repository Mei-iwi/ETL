"""Behavior checks for full-text bag ranking; model doubles prove plumbing only."""

import math
import unicodedata
from copy import deepcopy

import pytest

from data_sync_etl.domain.lexical import (
    RANKING_VERSION,
    analyze,
    bm25,
    contains_phrase,
    lexical_document,
    query_terms,
    snippet_text,
    surface_tokens,
)
from data_sync_etl.domain.search import SearchError
from tests.unit import test_search

searchable = test_search.searchable


def test_analyzer_unicode_compounds_mixed_terms_and_negation():
    text = 'Đại học Bách Khoa Hà Nội dạy lập trình C++ C# .NET Python3 năm 2026'
    tokens = analyze(text)
    assert tokens == analyze(unicodedata.normalize('NFD', text))
    assert {'đại', 'học', 'c++', 'c#', '.net', 'python3', '2026'} <= set(tokens)
    assert any('_' in term for term in tokens)
    assert {'python3', 'word42'} <= set(analyze('Python3 word42'))
    assert 'word' not in analyze('word42')
    assert 'không' in analyze('không đồng ý')
    assert query_terms('và của') == []
    assert query_terms('') == []
    assert analyze('ma má mà')[:3] == ['ma', 'má', 'mà']


def test_bm25_expected_tf_df_length_and_nonmatching_corpus():
    docs = {'a': lexical_document(['rare rare']), 'b': lexical_document(['rare filler']),
            'c': lexical_document(['other filler'])}
    scores = bm25('rare', docs)
    idf = math.log1p((3 - 2 + .5) / (2 + .5))
    assert scores['a'] == pytest.approx(idf * 2 * 2.2 / (2 + 1.2))
    assert scores['b'] == pytest.approx(idf)
    assert 'c' not in scores
    assert scores['a'] > scores['b']
    assert scores != bm25('rare', {key: docs[key] for key in ('a', 'b')})
    assert bm25('rare rare', docs) == scores
    assert bm25('missing', docs) == {}
    assert bm25('và của', docs) == {}
    long = lexical_document(['rare ' + 'filler ' * 80])
    assert bm25('rare', {'short': docs['b'], 'long': long})['short'] > bm25(
        'rare', {'short': docs['b'], 'long': long})['long']


def test_title_weight_and_exact_phrase_do_not_cross_leaf_boundaries():
    docs = {'title': lexical_document(['filler'], 'rare'),
            'body': lexical_document(['rare filler'])}
    assert bm25('rare', docs)['title'] > bm25('rare', docs)['body']
    exact = lexical_document(['học máy không giám sát'])
    reordered = lexical_document(['máy học sát không giám'])
    assert bm25('không giám sát', {'exact': exact, 'other': reordered})['exact'] > bm25(
        'không giám sát', {'exact': exact, 'other': reordered})['other']
    boundary = lexical_document(['học', 'máy'])
    assert not any(contains_phrase(p, surface_tokens('học máy')) for p in boundary.passages)


@pytest.mark.parametrize('query,text', [
    ('machine learning and the', 'the plants and the sunlight'),
    ('học máy trong tài liệu', 'trong vườn có cây và hoa'),
    ('How to bake a chocolate cake in an oven', 'How to configure PostgreSQL and Python'),
])
def test_function_word_overlap_alone_cannot_admit_a_lexical_hit(query, text):
    assert bm25(query, {'unrelated': lexical_document([text])}) == {}


@pytest.mark.parametrize('searchable', [{'texts': ['alpha beta gamma delta omega ' * 2 + 'xylophone']}], indirect=True)
def test_rare_word_long_query_and_bow_never_encodes(searchable):
    c, _ = searchable
    bag = next(iter(c.indexing.bags.rows.values()))
    assert 'xylophone' not in bag.keywords
    assert 'xylophone' in bag.lexical.terms

    def forbidden(*_):
        pytest.fail('BoW invoked the embedding model')

    c.indexing.embedding.prepare = forbidden
    c.indexing.embedding.embed = forbidden
    for query in ['xylophone', 'unknown1 unknown2 unknown3 unknown4 unknown5 unknown6 xylophone']:
        result = c.search.search(query, 'bow')
        assert result['total'] == 1
        assert result['ranking_algorithm'] == RANKING_VERSION
        assert result['items'][0]['matched_keywords'] == ['xylophone']
        assert result['items'][0]['score_components']['bow'] > 0
        assert 'xylophone' in result['items'][0]['snippet']


def test_snippet_centers_a_late_match():
    text = 'unrelated ' * 100 + 'xylophone learning'
    assert 'xylophone' in snippet_text(text, {'xylophone'})
    text = 'học ' + 'unrelated ' * 100 + 'học máy không giám sát'
    assert 'học máy không giám sát' in snippet_text(text, {'học', 'máy'}, query='học máy không giám sát')


@pytest.mark.parametrize('query', ['zircon lattice', 'réseau cristallin'])
def test_multilingual_phrases_retrieve_only_via_reviewed_corpus(tmp_path, query):
    from tests.corpus_dictionary_support import reviewed_dictionary
    docs = {'relevant': lexical_document(['Mạng tinh thể cần nghiên cứu.']),
            'other': lexical_document(['Cây xanh cần ánh sáng.'])}
    assert bm25(query, docs) == {}
    with reviewed_dictionary(tmp_path,
            'vi: mạng tinh thể | en: zircon lattice | fr: réseau cristallin', query) as dictionary:
        assert set(bm25(query, docs, dictionary=dictionary)) == {'relevant'}


def test_equivalences_are_bidirectional_and_literal_evidence_wins(tmp_path):
    from tests.corpus_dictionary_support import reviewed_dictionary
    docs = {'fr': lexical_document(['réseau cristallin']), 'en': lexical_document(['zircon lattice'])}
    with reviewed_dictionary(tmp_path, 'en: zircon lattice | fr: réseau cristallin', 'zircon lattice') as dictionary:
        assert bm25('zircon lattice', docs, dictionary=dictionary)['en'] > bm25(
            'zircon lattice', docs, dictionary=dictionary)['fr'] > 0
        assert bm25('réseau cristallin', docs, dictionary=dictionary)['fr'] > bm25(
            'réseau cristallin', docs, dictionary=dictionary)['en'] > 0


def test_equivalences_keep_negation_boundaries_and_ambiguity():
    from data_sync_etl.domain.concepts import concept_frequencies
    assert not concept_frequencies(surface_tokens('unsupervised learning'))
    assert bm25('unsupervised learning', {'s': lexical_document(['học có giám sát'])}) == {}
    assert bm25('machine learning', {'split': lexical_document(['học', 'máy'])}) == {}
    assert bm25('machine learning', {'partial': lexical_document(['máy tính'])}) == {}
    assert bm25('database', {'identifier': lexical_document(['mydatabase'])}) == {}
    assert bm25('bank', {'vi': lexical_document(['ngân hàng bờ sông'])}) == {}


@pytest.mark.parametrize('searchable', [{'texts': [
    'unrelated evidence\n' * 15 + 'Zircon lattice supports spectral analysis.',
]}], indirect=True)
def test_multilingual_bow_search_returns_source_evidence_and_snippet(searchable, tmp_path):
    from tests.corpus_dictionary_support import reviewed_dictionary
    c, _ = searchable
    c.indexing.embedding.prepare = lambda: pytest.fail('BoW loaded a model')
    c.indexing.embedding.embed = lambda *_: pytest.fail('BoW encoded a query')
    assert c.search.search('réseau cristallin', 'bow')['total'] == 0
    with reviewed_dictionary(tmp_path, 'en: zircon lattice | fr: réseau cristallin', 'réseau cristallin') as dictionary:
        c.search.dictionary = dictionary
        result = c.search.search('réseau cristallin', 'bow')
        assert result['total'] == 1
        assert {'zircon', 'lattice'} <= set(result['items'][0]['matched_keywords'])
        assert 'Zircon lattice' in result['items'][0]['snippet']
        assert result['items'][0]['score_components']['bow'] > 0


def test_extension_manifest_matches_versioned_contract_and_preserves_foundation():
    import json
    from pathlib import Path

    from data_sync_etl.adapters.mongo.lexical_schema import LEGACY_ANALYZER, lexical_contract
    from data_sync_etl.adapters.mongo.schema import contracts
    from data_sync_etl.domain.indexing import digest

    manifest = json.loads((Path(__file__).parents[2] / 'migrations_mongo/lexical_v1.json').read_text())
    assert manifest['validator_sha256'] == digest(lexical_contract(384, analyzer=LEGACY_ANALYZER))
    assert manifest['collections_added'] == manifest['indexes_added'] == []
    extended = lexical_contract(384)['$jsonSchema']['properties']
    base = contracts(384)['content_bags']['$jsonSchema']['properties']
    assert {key: value for key, value in extended.items() if key != 'lexical'} == base


def test_legacy_schema_recognition_rejects_unknown_changes():
    import json
    from pathlib import Path

    from data_sync_etl.adapters.mongo.lexical_schema import is_legacy_concept_contract
    from data_sync_etl.adapters.mongo.schema import contracts

    validator = contracts(1024)['content_bags']
    validator['$jsonSchema']['properties']['concepts'] = json.loads((Path(__file__).parents[2]
        / 'fixtures/search/legacy_concept_property.json').read_text())
    assert is_legacy_concept_contract(validator, 1024)
    assert not is_legacy_concept_contract(validator, 384)
    validator['$jsonSchema']['properties']['concepts']['additionalProperties'] = True
    assert not is_legacy_concept_contract(validator, 1024)


def test_builder_bson_keeps_required_lexical_defaults(searchable):
    c, _ = searchable
    for bag in c.indexing.bags.rows.values():
        payload = bag.bson()['lexical']
        assert payload['version'] == bag.lexical.version
        assert payload['analyzer'] == bag.lexical.analyzer
        assert set(payload) == set(type(bag.lexical).model_fields)


def test_legacy_wrong_analyzer_or_tf_is_not_ready(searchable):
    c, version = searchable
    key = next(iter(c.indexing.bags.rows))
    original = deepcopy(c.indexing.bags.rows[key])
    for change in ('missing', 'analyzer', 'tf'):
        bag = deepcopy(original)
        if change == 'missing':
            bag.lexical = None
        elif change == 'analyzer':
            bag.lexical.analyzer = 'obsolete-analyzer'
        else:
            bag.lexical.frequencies[0] += 1
        c.indexing.bags.rows[key] = bag
        assert c.indexing.status(version['id'])['index_status'] == 'STALE'
        result = c.search.search('page', 'bow')
        assert result['total'] == 0 and result['readiness']['reindex_required']
    c.indexing.bags.rows[key] = original


def test_backfill_preflight_reuses_dense_without_encoder(searchable):
    from scripts.reindex_lexical import prepare_version

    c, version = searchable
    rows = [doc.bson() for doc in c.indexing.bags.rows.values()]
    for row in rows:
        row.pop('lexical')
    before = deepcopy(rows)
    c.indexing.embedding.embed = lambda *_: pytest.fail('Lexical backfill encoded content')
    _, updated = prepare_version(c.indexing, version['id'], rows)
    assert rows == before
    assert all(row['lexical']['terms'] for row in updated)
    assert [row['embedding'] for row in updated] == [row['embedding'] for row in rows]
    rows[0]['embedding_model'] = 'wrong@revision'
    with pytest.raises(ValueError, match='mismatch'):
        prepare_version(c.indexing, version['id'], rows)


def test_nonhit_corpus_change_cannot_publish_old_bm25_scores(searchable, monkeypatch):
    c, _ = searchable
    from data_sync_etl.application import search as module

    original = module.bm25

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        # Every bag is in the statistics scope, even if absent from the hit set.
        for bag in c.indexing.bags.rows.values():
            bag.lexical.frequencies[0] += 1
        return result

    monkeypatch.setattr(module, 'bm25', mutate)
    with pytest.raises(SearchError):
        c.search.search('missing', 'bow')
