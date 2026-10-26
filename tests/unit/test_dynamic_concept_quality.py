"""Draft synthetic development examples, NOT independently reviewed qrels."""
import json
from dataclasses import replace
from pathlib import Path
from time import perf_counter

import pytest

from data_sync_etl.adapters.concept_artifacts import ArtifactStore
from data_sync_etl.adapters.concept_corpus import FixtureCorpus
from data_sync_etl.application.concept_discovery import ConceptDiscoveryWorker
from data_sync_etl.domain.concepts import default_dictionary
from data_sync_etl.domain.corpus_concepts import discover
from data_sync_etl.main import create_app
from data_sync_etl.ports.concept_corpus import ConceptBlocked
from tests.local_admin_client import TestClient
from tests.unit.test_dynamic_concept_dictionary import activate, approve, resource
from tests.unit.test_dynamic_concept_dictionary import pipeline as pipeline
from tests.unit.test_search import searchable as searchable


def test_reviewed_vi_en_fr_equivalence_merges_without_alias_stacking(tmp_path):
    inventory = FixtureCorpus([resource(1), resource(2, fields={'title': 'Mémoire tampon (cache)'})])
    store = ArtifactStore(tmp_path / 'multilingual', create=True)
    worker = ConceptDiscoveryWorker(inventory, store, clock=lambda: 100.)
    worker.tick()
    for record in worker.contributions():
        for candidate in record['relations']:
            approve(worker, candidate)
    provider = activate(inventory, store, worker)
    for query in ('cache', 'bộ nhớ đệm', 'mémoire tampon', 'quá trình cache'):
        with provider.pin(query) as dictionary:
            terms = {term.term for term in dictionary.expand(query).terms}
            assert {'cache', 'bộ nhớ đệm', 'mémoire tampon'} <= terms
    assert store.load().manifest['count'] == 1


def test_seed_identity_preserved_and_no_cooccurrence_auto_relation():
    result = discover(resource(domain='botany', fields={'title': 'Quang hợp (photosynthesis)'}))
    assert result['relations'][0]['concept_id'] == 'etl:photosynthesis'
    assert not discover(resource(fields={'title': 'Cache and memory appear together in this lesson.'}))['relations']
    definition = discover(resource(fields={'title': 'memory cache means cache memory'}))
    assert definition['relations'][0]['kind'] == 'synonym'
    assert definition['relations'][0]['status'] == 'pending'


def test_one_hundred_versions_do_not_inflate_concept_count_or_runtime_provenance(tmp_path):
    inventory = FixtureCorpus([resource(index) for index in range(100)])
    store = ArtifactStore(tmp_path / 'dedup', create=True)
    worker = ConceptDiscoveryWorker(inventory, store, batch_size=100, tick_seconds=30., clock=lambda: 100.)
    assert worker.tick()['status'] == 'CANDIDATE_ONLY'
    for record in worker.contributions():
        approve(worker, record['relations'][0])
    manifest = worker.build()
    assert manifest['count'] == 1 and manifest['coverage']['approved_relations'] == 100
    concept = store.load(manifest['generation']).lookup(['cache'], budget_ms=100)[0]
    assert len(concept['sources']) <= 8


def test_candidate_modified_after_review_cannot_reuse_old_approval(pipeline):
    _, store, worker, candidate = pipeline
    approve(worker, candidate)
    key = worker.contribution_key('r001')
    record = store.read_json(key)
    record['relations'][0]['terms'][0]['text'] = 'unreviewed alias'
    store.write_json(key, record)
    with pytest.raises(ConceptBlocked, match='BINDING'):
        worker.build()


def test_same_version_extension_change_updates_contribution_and_invalidates_review(pipeline):
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    original = inventory.current('r001')
    inventory.put(replace(original, revision=2, fields={'title': 'Mémoire tampon (cache)'}))
    worker.reconcile()
    assert worker.tick()['status'] == 'CANDIDATE_ONLY'
    assert store.read_json(worker.contribution_key('r001'))['fingerprint'] != discover(original)['fingerprint']
    assert worker.build()['count'] == 0


@pytest.mark.parametrize('searchable', [{'texts': ['Cache makes repeated data lookups faster.']}], indirect=True)
def test_http_final_send_fence_blocks_late_private_alias(searchable, pipeline, monkeypatch):
    c, _ = searchable
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    c.search.concept_resolver = activate(inventory, store, worker)
    original = c.search.search_http
    def revoke_after_result(**kwargs):
        result = original(**kwargs)
        inventory.put(resource(revision=2, public=False))
        return result
    monkeypatch.setattr(c.search, 'search_http', revoke_after_result)
    with TestClient(create_app(c)) as api:
        response = api.post('/api/v1/search', json={'query': 'bộ nhớ đệm', 'method': 'bow'})
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'RESOURCE_VISIBILITY_CHANGED'
        assert 'cache' not in response.text


@pytest.mark.parametrize('searchable', [{'texts': ['Cache makes repeated data lookups faster.']}], indirect=True)
def test_synthetic_http_before_after_latency_and_negative_queries(searchable, pipeline, tmp_path):
    from scripts.benchmark_concept_artifacts import quantiles
    c, _ = searchable
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    provider = activate(inventory, store, worker)
    c.search.concept_resolver = provider
    report = {'scope': 'synthetic_sqlite_asgi_with_embedding_test_double', 'independent_qrels': False,
              'source_head': '70f5146', 'results': {}}
    with TestClient(create_app(c)) as api:
        for enabled in (False, True):
            provider.enabled = enabled
            for method in ('bow', 'vector', 'hybrid'):
                timings, counts = [], []
                for _ in range(8):
                    started = perf_counter()
                    response = api.post('/api/v1/search', json={'query': 'bộ nhớ đệm', 'method': method})
                    timings.append((perf_counter() - started) * 1000)
                    assert response.status_code == 200, response.text
                    counts.append(response.json()['total'])
                report['results'][f'{method}-dynamic-{enabled}'] = {'latency_ms': quantiles(timings), 'totals': counts}
        for query in ('quantum dinosaur astronomy', 'ngành khoa học chưa có xyz', 'unrelatedzznonexistent'):
            assert api.post('/api/v1/search', json={'query': query, 'method': 'bow'}).json()['total'] == 0
    output = Path(tmp_path) / 'http-performance.json'
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    assert report['results']['bow-dynamic-False']['totals'] == [0] * 8
    assert report['results']['bow-dynamic-True']['totals'] == [1] * 8
    assert provider.base.fingerprint == default_dictionary().fingerprint
