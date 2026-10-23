import pytest

from tests.unit.test_search import searchable as searchable


@pytest.mark.parametrize('query', ['green leaf', 'green leaves', 'lá xanh'])
@pytest.mark.parametrize('searchable', [{'texts': ['Lá xanh chứa diệp lục và hấp thụ ánh sáng.']}], indirect=True)
def test_bow_bilingual_concept_keeps_original_source_context(searchable, monkeypatch, query):
    container, _ = searchable
    monkeypatch.setattr(container.indexing.embedding, 'embed',
        lambda *_: pytest.fail('BoW must not invoke an encoder'))
    result = container.search.search(query, 'bow')
    assert result['total'] == 1
    item = result['items'][0]
    assert 'Lá xanh' in item['snippet']
    assert item['context_evidence'] == 'lexical'
    assert 'vector' not in item['score_components']
    context = container.search.context(item['context_id'], neighbors=0)
    assert item['snippet'] in context['chunks'][0]['text']


@pytest.mark.parametrize('method', ['vector', 'hybrid'])
@pytest.mark.parametrize('meaning,context,enabled,expected', [
    (.58, .54, True, 1), (.55, .45, True, 1),
    (.58, .44, True, 0), (.54, .90, True, 0),
    (.58, .90, False, 0), (.40, .90, True, 0),
])
@pytest.mark.parametrize('searchable', [{'meanings': True,
    'texts': ['Lá xanh chứa diệp lục và hấp thụ ánh sáng.']}], indirect=True)
def test_dense_near_miss_requires_same_bag_context_without_lexical_overlap(
        searchable, monkeypatch, method, meaning, context, enabled, expected):
    container, _ = searchable
    container.settings.search_meaning_context_fallback_enabled = enabled
    def match(vector, bag, **kwargs):
        member = next(member for member in bag.members if member.kind == 'text')
        return meaning, member, [member.text]
    monkeypatch.setattr('data_sync_etl.application.search.match_meaning', match)
    monkeypatch.setattr('data_sync_etl.application.search.cosine', lambda *_: context)
    result = container.search.search('foreign-language-description', method)
    assert result['total'] == expected
    if expected:
        item = result['items'][0]
        assert item['score_components'] == {'vector': meaning}
        assert item['matched_keywords'] == []
        assert item['context_evidence'] == 'semantic'
        selected = container.search.context(item['context_id'], neighbors=0)['chunks'][0]
        assert item['snippet'] in selected['text']
