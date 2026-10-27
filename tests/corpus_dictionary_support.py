"""ISOLATED_TEST_ONLY corpus → separate reviews → published artifact → resolver.

No bundled vocabulary is imported or reproduced by this helper.
"""
from contextlib import contextmanager

from data_sync_etl.adapters.concept_artifacts import ArtifactStore
from data_sync_etl.adapters.concept_corpus import FixtureCorpus
from data_sync_etl.application.concept_discovery import ConceptDiscoveryWorker
from tests.unit.test_dynamic_concept_dictionary import activate, approve, resource


@contextmanager
def reviewed_dictionary(tmp_path, source, query, *, domain='fixture-domain', sense=''):
    inventory = FixtureCorpus([resource(domain=domain, sense=sense, fields={'title': source})])
    store = ArtifactStore(tmp_path / 'ISOLATED_TEST_ONLY', create=True)
    worker = ConceptDiscoveryWorker(inventory, store)
    worker.tick()
    for contribution in worker.contributions():
        for candidate in contribution['relations']:
            approve(worker, candidate)
    provider = activate(inventory, store, worker)
    try:
        with provider.pin(query, domain) as dictionary:
            yield dictionary
    finally:
        provider.close()
