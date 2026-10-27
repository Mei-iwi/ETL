"""P06 development-only corpus examples, never independently judged qrels."""
from data_sync_etl.domain.concepts import empty_dictionary
from tests.unit.test_dynamic_concept_dictionary import activate, approve
from tests.unit.test_dynamic_concept_dictionary import pipeline as pipeline


def test_long_raw_query_can_resolve_reviewed_anchor_without_truncation(pipeline):
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    provider = activate(inventory, store, worker)
    query = 'Explain ' * 35 + 'cache'
    try:
        with provider.pin(query) as dictionary:
            assert dictionary is not empty_dictionary()
            assert dictionary.expand(query).original == query.strip()
            assert 'bộ nhớ đệm' in {term.term for term in dictionary.expand(query).terms}
    finally:
        provider.close()


def test_source_declared_accentless_variant_requires_separate_reviews(tmp_path):
    from data_sync_etl.adapters.concept_artifacts import ArtifactStore
    from data_sync_etl.adapters.concept_corpus import FixtureCorpus
    from data_sync_etl.application.concept_discovery import ConceptDiscoveryWorker
    from tests.unit.test_dynamic_concept_dictionary import resource
    inventory = FixtureCorpus([resource(fields={'title': 'Bộ nhớ đệm viết không dấu là bo nho dem'})])
    store = ArtifactStore(tmp_path / 'approved-spelling', create=True)
    worker = ConceptDiscoveryWorker(inventory, store)
    worker.tick()
    candidate = next(worker.contributions())['relations'][0]
    assert candidate['kind'] == 'spelling' and worker.build()['count'] == 0
    approve(worker, candidate)
    provider = activate(inventory, store, worker)
    try:
        with provider.pin('bo nho dem') as dictionary:
            assert 'bộ nhớ đệm' in {term.term for term in dictionary.expand('bo nho dem').terms}
        with provider.pin('bo nho dam') as dictionary:
            assert dictionary is empty_dictionary()
    finally:
        provider.close()
