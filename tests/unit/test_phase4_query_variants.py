"""Accent restoration requires corpus approval and never rewrites Vector input."""
import unicodedata

import pytest
from pydantic import ValidationError

from data_sync_etl.api.search_schemas import SearchRequest
from data_sync_etl.domain.concepts import Dictionary, ExpansionPolicy, empty_dictionary
from data_sync_etl.domain.query_variants import approved_accent_variant, fold
from tests.corpus_dictionary_support import reviewed_dictionary
from tests.unit.test_search import searchable as searchable
from tests.unit.test_semantic import semantic_search as semantic_search


def variant(query, dictionary=None, **kwargs):
    return approved_accent_variant(query, dictionary or empty_dictionary(), policy=ExpansionPolicy(), **kwargs)


def test_only_corpus_approved_missing_accents_are_restored(tmp_path):
    assert variant('mang tinh the') is None
    with reviewed_dictionary(tmp_path, 'Mạng tinh thể viết không dấu là mang tinh the', 'mang tinh the') as dictionary:
        assert variant('mang tinh the', dictionary) == 'mạng tinh thể'
        assert variant('khong mang tinh the C++ C#', dictionary) == 'khong mạng tinh thể C++ C#'
        assert variant('mạng tinh thể', dictionary) is None
        assert variant('màng tinh the', dictionary) is None
        assert variant('mang-tinh-the', dictionary) is None
        assert variant('HL26_mang_tinh_the', dictionary) is None
        assert variant('mang tinh the', dictionary, domain='other-domain') is None
    assert variant('ma bo nam') is None
    assert fold('Đường') == 'duong'


def test_pending_low_confidence_and_ambiguous_phrases_cannot_restore_accents(tmp_path):
    with reviewed_dictionary(tmp_path, 'Mạng tinh thể viết không dấu là mang tinh the', 'mang tinh the') as dictionary:
        snapshot = dictionary.snapshot.model_copy(deep=True)
        for term in snapshot.concepts[0].terms:
            term.review_status = 'pending'
        assert variant('mang tinh the', Dictionary(snapshot)) is None
        for term in snapshot.concepts[0].terms:
            term.review_status, term.confidence = 'approved', .1
        assert variant('mang tinh the', Dictionary(snapshot)) is None
        snapshot = dictionary.snapshot.model_copy(deep=True)
        other = snapshot.concepts[0].model_copy(deep=True)
        other.concept_id += ':ambiguous'
        for term in other.terms:
            term.concept_id = other.concept_id
        snapshot.concepts.append(other)
        assert variant('mang tinh the', Dictionary(snapshot)) is None


@pytest.mark.parametrize('method', ['vector', 'hybrid'])
@pytest.mark.parametrize('searchable', [{'texts': ['Mạng tinh thể.']}], indirect=True)
def test_vector_always_encodes_raw_query_even_with_approved_lexical_variant(searchable, tmp_path, monkeypatch, method):
    c, _ = searchable
    encoded = []
    def encode(text):
        encoded.append(text)
        return [.1, .2, .3]
    monkeypatch.setattr(c.indexing.embedding, 'embed', encode)
    with reviewed_dictionary(tmp_path, 'Mạng tinh thể viết không dấu là mang tinh the', 'mang tinh the') as dictionary:
        c.search.dictionary = dictionary
        c.settings.search_approved_accent_variants_enabled = True
        result = c.search.search('mang tinh the', method)
        assert encoded == ['mang tinh the']
        assert result['query'] == 'mang tinh the'
        assert result['total'] == 1


@pytest.mark.parametrize('text', ['C++ C# CO₂ CO2 H2O', 'HL26_R013 HL26_V010', '🙂', '!!!',
    'khong the loc muoi hoa tan', 'toan', 'quang hơp', 'cell trong Excel', 'cell trong sinh học',
    'cach add fractions khac mau', 'quang hop photosynthesis photosynthèse', 'Comment additionner des fractions ?',
    'base de données', 'ĐƯỜNG', 'co so du lieu   ', 'ＡＢＣ русский', 'word '*30, '\u0301', 'abc\x00def', 'abc\x1fdef'])
def test_unicode_symbols_negation_and_unusual_inputs_are_not_globally_folded(text):
    request = SearchRequest(query=text)
    assert request.query == unicodedata.normalize('NFC', text).strip()


@pytest.mark.parametrize('text', ['', '   ', 'x'*2001, '\ud800'])
def test_invalid_search_lengths_remain_explicit_errors(text):
    with pytest.raises(ValidationError):
        SearchRequest(query=text)


def test_semantic_multi_query_reads_one_generation_and_never_stacks_duplicate_scores(semantic_search, monkeypatch):
    from data_sync_etl.application.content_bags import bag_structure
    c, version, service = semantic_search
    service.build(version['id'], apply=True)
    snapshot, metadata = service._snapshot(version['id'])
    roots = bag_structure(snapshot[2])
    allowed = {row['id'] for row in snapshot[2]}
    first, second = [1.] + [0.]*1023, [0., 1.] + [0.]*1022
    base = service.scores(snapshot, metadata, roots, allowed, first, lambda: None, [0])
    reads = []
    original = service.store.records
    monkeypatch.setattr(service.store, 'records', lambda *args: (reads.append(args) or original(*args)))
    merged = service.scores(snapshot, metadata, roots, allowed, first, lambda: None, [0],
                            query_vectors=[first])
    assert len(reads) == 1
    assert merged == base
    assert all(hit[0] <= 1 for hit in merged.values())
    assert service.scores(snapshot, metadata, {}, set(), first, lambda: None, [0], query_vectors=[second]) == {}
    from data_sync_etl.domain.indexing import IndexingError
    with pytest.raises(IndexingError, match='SEMANTIC_QUERY_VARIANT_LIMIT'):
        service.scores(snapshot, metadata, roots, allowed, first, lambda: None, [0], query_vectors=[first, second])


@pytest.mark.parametrize('searchable', [{'texts': ['Lá xanh.']}], indirect=True)
def test_variant_flag_never_changes_resource_grants(searchable):
    from data_sync_etl.domain.search_access import SearchAccess
    c, _ = searchable
    c.settings.search_approved_accent_variants_enabled = True
    for method in ('bow', 'vector', 'hybrid'):
        result = c.search.search('la xanh', method, access=SearchAccess('authenticated_user', actor_id='no-grant'))
        assert result['total'] == 0
