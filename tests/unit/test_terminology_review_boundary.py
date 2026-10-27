"""Review gates remain effective after removal of the global vocabulary."""
from data_sync_etl.domain.concepts import Dictionary
from tests.corpus_dictionary_support import reviewed_dictionary


def test_pending_or_low_confidence_context_cannot_choose_an_acronym_sense(tmp_path):
    with reviewed_dictionary(tmp_path, 'zircon lattice (ZL)', 'zircon lattice') as dictionary:
        snapshot = dictionary.snapshot.model_copy(deep=True)
        concept = snapshot.concepts[0]
        term = concept.terms[0].model_copy(deep=True, update={
            'text': 'unreviewed alias', 'normalized': 'unreviewed alias',
            'kind': 'synonym', 'context_hints': ['untrustedclue'], 'review_status': 'pending'})
        concept.terms.append(term)
        assert not Dictionary(snapshot).expand('ZL untrustedclue').terms
        term.review_status = 'approved'
        term.confidence = .1
        assert not Dictionary(snapshot).expand('ZL untrustedclue').terms
        term.confidence = .95
        assert 'zircon lattice' in {t.term for t in Dictionary(snapshot).expand('ZL untrustedclue').terms}


def test_context_ranker_reuses_explicit_dictionary_instead_of_expanding_default_again(tmp_path, monkeypatch):
    from data_sync_etl.domain.context_ranking import ContextRanker
    with reviewed_dictionary(tmp_path, 'en: zircon lattice | fr: réseau cristallin', 'zircon lattice') as dictionary:
        expansion = dictionary.expand('zircon lattice')
        monkeypatch.setattr('data_sync_etl.domain.context_ranking.concept_frequencies',
                            lambda *_: (_ for _ in ()).throw(AssertionError('Redundant expansion')))
        ranker = ContextRanker('zircon lattice', ['zircon', 'lattice'], ['réseau cristallin', 'unrelated'],
                              dictionary=dictionary, expansion=expansion)
        assert ranker.score(0) > ranker.score(1)
