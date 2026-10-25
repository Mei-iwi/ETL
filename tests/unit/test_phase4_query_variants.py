import unicodedata

import pytest
from pydantic import ValidationError

from data_sync_etl.api.search_schemas import SearchRequest
from data_sync_etl.domain.concepts import Dictionary, ExpansionPolicy, default_dictionary
from data_sync_etl.domain.query_variants import approved_accent_variant, fold
from tests.unit.test_search import searchable as searchable
from tests.unit.test_semantic import semantic_search as semantic_search


def variant(query, dictionary=None, **kwargs):
    return approved_accent_variant(query, dictionary or default_dictionary(), policy=ExpansionPolicy(), **kwargs)


def test_only_missing_accents_are_restored_and_original_syntax_is_preserved():
    assert variant('co so du lieu') == 'cơ sở dữ liệu'
    assert variant('cách cong hai phân số') == 'cách cộng hai phân số'
    assert variant('khong co so du lieu C++ C# CO₂ HL26_R013') == 'khong cơ sở dữ liệu C++ C# CO₂ HL26_R013'
    assert variant('cơ sở dữ liệu') is None
    assert variant('có so du lieu') is None  # Explicit accent contradicts the approved phrase.
    assert variant('co-so-du-lieu') is None
    assert variant('HL26_co_so_du_lieu') is None
    assert variant('co so du lieu', domain='biology') is None
    assert variant('ma bo nam') is None  # Never turn single-word collisions into synonyms.
    assert fold('Đường') == 'duong'


def test_pending_low_confidence_and_ambiguous_phrases_cannot_restore_accents():
    snapshot = default_dictionary().snapshot.model_copy(deep=True)
    concept = next(c for c in snapshot.concepts if any(t.normalized == 'cơ sở dữ liệu' for t in c.terms))
    for term in concept.terms:
        term.review_status = 'pending'
    assert variant('co so du lieu', Dictionary(snapshot)) is None
    for term in concept.terms:
        term.review_status, term.confidence = 'approved', .1
    assert variant('co so du lieu', Dictionary(snapshot)) is None
    snapshot = default_dictionary().snapshot.model_copy(deep=True)
    concept = next(c for c in snapshot.concepts if any(t.normalized == 'cơ sở dữ liệu' for t in c.terms))
    other = concept.model_copy(deep=True)
    other.concept_id = 'fixture:ambiguous'
    for term in other.terms:
        term.concept_id = other.concept_id
    snapshot.concepts.append(other)
    assert variant('co so du lieu', Dictionary(snapshot)) is None


@pytest.mark.parametrize('method', ['bow', 'vector', 'hybrid'])
@pytest.mark.parametrize('searchable', [{'texts': ['Lá xanh.']}, {'texts': ['Lá xanh.'], 'meanings': True}], indirect=True)
def test_native_empty_is_reproduced_and_opt_in_uses_bounded_variants_with_real_context(searchable, monkeypatch, method):
    c, version = searchable
    encoded = []
    def encode(text):
        encoded.append(text)
        return [.1, .2, .3] if 'lá xanh' in text.casefold() else [1., 0., 0.]
    monkeypatch.setattr(c.indexing.embedding, 'embed', encode)
    baseline_total = 0 if method == 'vector' else 1  # The literal word "xanh" already gives lexical recall.
    baseline = c.search.search('la xanh', method)
    assert baseline['total'] == baseline_total
    assert encoded == ([] if method == 'bow' else ['la xanh'])
    encoded.clear()
    c.settings.search_approved_accent_variants_enabled = True
    result = c.search.search('la xanh', method)
    assert result['total'] == 1
    assert encoded == ([] if method == 'bow' else ['la xanh', 'lá xanh'])
    assert result['query'] == 'la xanh'
    assert result['query_expansion']['retrieval_queries'] == ['la xanh', 'lá xanh']
    assert result['query_expansion']['lexical_variant_weight'] == c.settings.search_expansion.synonyms
    if method == 'bow':
        c.settings.search_approved_accent_variants_enabled = False
        native = c.search.search('lá xanh', method)
        c.settings.search_approved_accent_variants_enabled = True
        assert result['items'][0]['score'] == pytest.approx(max(
            baseline['items'][0]['score'], c.settings.search_expansion.synonyms * native['items'][0]['score']))
    assert 'approved-accent' in result['ranking_algorithm']
    item = result['items'][0]
    assert item['resource_version_id'] == version['id']
    context = c.search.context(item['context_id'], neighbors=0)
    assert any(item['snippet'] in chunk['text'] for chunk in context['chunks'])
    c.settings.search_approved_accent_variants_enabled = False
    assert c.search.search('la xanh', method)['total'] == baseline_total  # Rollback, no reindex.


@pytest.mark.parametrize('text', ['C++ C# CO₂ CO2 H2O', 'HL26_R013 HL26_V010', '🙂', '!!!',
    'khong the loc muoi hoa tan', 'toan', 'quang hơp', 'cell trong Excel', 'cell trong sinh học',
    'cach add fractions khac mau', 'quang hop photosynthesis photosynthèse', 'Comment additionner des fractions ?',
    'base de données', 'ĐƯỜNG', 'co so du lieu   ', 'ＡＢＣ русский', 'word '*30, '\u0301', 'abc\x00def', 'abc\x1fdef'])
def test_unicode_symbols_negation_and_unusual_inputs_are_not_globally_folded(text):
    request = SearchRequest(query=text)
    assert request.query == unicodedata.normalize('NFC', text).strip()


@pytest.mark.parametrize('text', ['', '   ', 'x'*301, '\ud800'])
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
