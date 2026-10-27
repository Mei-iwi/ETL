"""Checkpoint 05: development labels, not independently reviewed retrieval qrels."""
import unicodedata

import pytest

from data_sync_etl.application.concept_discovery import reviewed_concept
from data_sync_etl.domain.corpus_concepts import discover, safe_phrase, valid_relation
from tests.unit.test_dynamic_concept_dictionary import resource


def test_explicit_vi_en_fr_candidates_are_pending_and_bound_to_source():
    row = resource(domain='math', fields={'title':
        'vi: bội chung nhỏ nhất | en: least common multiple | fr: plus petit commun multiple'})
    result = discover(row)
    assert len(result['relations']) == 3
    for candidate in result['relations']:
        assert candidate['kind'] == 'translation' and candidate['status'] == 'pending'
        assert candidate['rank_is_probability'] is False
        assert valid_relation(candidate, result['fingerprint'])
        assert reviewed_concept(candidate, 'draft-review-ledger', {}) is None


@pytest.mark.parametrize('text', ['memory cache is not cache memory', 'memory cache means not cache memory',
    'cache không phải là memory', 'cache ne signifie pas memory', 'Cache and memory appear together.'])
def test_negative_and_cooccurrence_do_not_become_relations(text):
    assert discover(resource(fields={'title': text}))['relations'] == []


def test_explicit_synonym_nfc_nfd_and_sense_do_not_merge_unrelated_cells():
    text = 'Mémoire tampon (cache)'
    a = discover(resource(fields={'title': text}))['relations'][0]
    b = discover(resource(fields={'title': unicodedata.normalize('NFD', text)}))['relations'][0]
    assert a['id'] == b['id']
    assert safe_phrase('hóa') != safe_phrase('hoa')
    labelled = 'vi: ô tế bào | en: cell'
    biology = discover(resource(domain='biology', sense='living cell', fields={'title': labelled}))['relations'][0]
    spreadsheet = discover(resource(domain='computing', sense='spreadsheet cell', fields={'title': labelled}))['relations'][0]
    assert biology['concept_id'] != spreadsheet['concept_id']
    synonyms = discover(resource(fields={'title': 'memory cache also called cache memory'}))['relations']
    assert synonyms[0]['kind'] == 'synonym' and synonyms[0]['status'] == 'pending'


@pytest.mark.parametrize('format', ['mp3', 'mp4'])
def test_media_metadata_only_candidate_never_requires_decoder(format):
    row = resource(fields={'title': 'vi: bộ nhớ đệm | en: cache', 'format': format}, ready=False)
    assert discover(row)['relations'][0]['kind'] == 'translation'
    assert discover(resource(public=False, fields=row.fields))['relations'] == []


def test_reviewed_senses_remain_separate_in_runtime_expansion(tmp_path):
    from data_sync_etl.adapters.concept_artifacts import ArtifactStore
    from data_sync_etl.adapters.concept_corpus import FixtureCorpus
    from data_sync_etl.application.concept_discovery import ConceptDiscoveryWorker
    from tests.unit.test_dynamic_concept_dictionary import activate, approve
    inventory = FixtureCorpus([
        resource(1, domain='biology', sense='living cell', fields={'title': 'vi: tế bào | en: cell'}),
        resource(2, domain='computing', sense='spreadsheet cell', fields={'title': 'vi: ô bảng tính | en: cell'})])
    store = ArtifactStore(tmp_path / 'senses', create=True)
    worker = ConceptDiscoveryWorker(inventory, store)
    worker.tick()
    for record in worker.contributions():
        for candidate in record['relations']:
            approve(worker, candidate)
    provider = activate(inventory, store, worker)
    try:
        for domain, required, rejected in [('biology', 'tế bào', 'ô bảng tính'), ('computing', 'ô bảng tính', 'tế bào')]:
            with provider.pin('cell', domain) as dictionary:
                terms = {term.term for term in dictionary.expand('cell', domain=domain).terms}
                assert required in terms and rejected not in terms
    finally:
        provider.close()


def test_reviewed_three_language_alignment_has_one_identity_without_alias_conflicts(tmp_path):
    from data_sync_etl.adapters.concept_artifacts import ArtifactStore
    from data_sync_etl.adapters.concept_corpus import FixtureCorpus
    from data_sync_etl.application.concept_discovery import ConceptDiscoveryWorker
    from tests.unit.test_dynamic_concept_dictionary import activate, approve
    labels = ['bội chung nhỏ nhất', 'least common multiple', 'plus petit commun multiple']
    inventory = FixtureCorpus([resource(domain='math', fields={'title':
        'vi: bội chung nhỏ nhất | en: least common multiple | fr: plus petit commun multiple'})])
    store = ArtifactStore(tmp_path / 'parallel', create=True)
    worker = ConceptDiscoveryWorker(inventory, store)
    worker.tick()
    candidates = next(worker.contributions())['relations']
    assert len({item['concept_id'] for item in candidates}) == 1
    for candidate in candidates:
        approve(worker, candidate)
    provider = activate(inventory, store, worker)
    try:
        assert store.load().manifest['count'] == 1
        for query in labels:
            with provider.pin(query, 'math') as dictionary:
                assert set(labels) <= {term.term for term in dictionary.expand(query, domain='math').terms}
    finally:
        provider.close()
