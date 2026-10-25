from data_sync_etl.domain.concepts import Dictionary, default_dictionary


def test_pending_or_low_confidence_context_cannot_choose_an_acronym_sense():
    snapshot = default_dictionary().snapshot.model_copy(deep=True)
    concept = next(c for c in snapshot.concepts if c.concept_id == 'etl:machine_learning')
    term = concept.terms[0].model_copy(deep=True, update={
        'text': 'unreviewed alias', 'normalized': 'unreviewed alias',
        'kind': 'synonym', 'context_hints': ['untrustedclue'], 'review_status': 'pending'})
    concept.terms.append(term)
    assert not Dictionary(snapshot).expand('ML untrustedclue').terms
    term.review_status = 'approved'
    term.confidence = .1
    assert not Dictionary(snapshot).expand('ML untrustedclue').terms
    term.confidence = .95
    assert 'machine learning' in {t.term for t in Dictionary(snapshot).expand('ML untrustedclue').terms}


def test_context_ranker_reuses_explicit_dictionary_instead_of_expanding_default_again(monkeypatch):
    from data_sync_etl.domain.context_ranking import ContextRanker
    dictionary = default_dictionary()
    expansion = dictionary.expand('học máy')
    monkeypatch.setattr('data_sync_etl.domain.context_ranking.concept_frequencies',
                        lambda *_: (_ for _ in ()).throw(AssertionError('Redundant default expansion')))
    ranker = ContextRanker('học máy', ['học', 'máy'], ['machine learning', 'unrelated'],
                          dictionary=dictionary, expansion=expansion)
    assert ranker.score(0) > ranker.score(1)
