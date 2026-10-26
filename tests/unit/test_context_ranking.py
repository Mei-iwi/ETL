from data_sync_etl.domain.context_ranking import ContextRanker
from data_sync_etl.domain.lexical import query_terms, snippet_text


def test_function_words_cannot_beat_actual_query_evidence():
    query = 'please explain the xylophone in the text'
    ranker = ContextRanker(query, query_terms(query), [
        'please the in the and for text', 'A xylophone produces musical sounds.',
    ])
    assert ranker.score(1) > ranker.score(0)


def test_exact_phrase_beats_translation_and_scattered_words():
    query = 'machine learning'
    ranker = ContextRanker(query, query_terms(query), [
        'machine tools for learning tasks', 'apprentissage automatique', 'machine learning',
    ])
    assert ranker.score(2) > ranker.score(1) > ranker.score(0)


def test_qualified_concepts_beat_partial_overlap():
    query = 'unsupervised learning'
    ranker = ContextRanker(query, query_terms(query), [
        'supervised learning learning learning', 'học không giám sát',
    ])
    assert ranker.score(1) > ranker.score(0)


def test_query_compounds_match_source_sequences_without_crossing_words():
    ranker = ContextRanker('học máy', ['học', 'máy', 'học_máy'], [
        'học sử dụng một cái máy', 'học máy dự đoán dữ liệu', 'máy móc trong giờ học'])
    assert ranker.score(1) > ranker.score(0)
    assert ranker.score(1) > ranker.score(2)


def test_unmatched_semantic_bag_has_no_passage_evidence():
    query = 'find a target by halving the interval'
    ranker = ContextRanker(query, query_terms(query), ['the and for', 'Học máy dự đoán.'])
    assert not any(ranker.score(0))
    assert not any(ranker.score(1))


def test_snippet_skips_function_words_before_a_late_hit():
    text = 'the and in ' * 100 + 'xylophone produces music'
    assert 'xylophone' in snippet_text(text, set(query_terms('the xylophone in')), query='the xylophone in')


def test_snippet_prefers_distinctive_query_word_to_repeated_generic_hits():
    text = 'text filler ' * 100 + 'xylophone produces music'
    assert 'xylophone' in snippet_text(text, {'text', 'xylophone'}, query='explain xylophone in text')


def test_snippet_finds_cjk_phrase_inside_a_long_unsegmented_passage():
    text = '背景内容' * 200 + '机器学习方法'
    assert '机器学习' in snippet_text(text, set(), query='học máy')
