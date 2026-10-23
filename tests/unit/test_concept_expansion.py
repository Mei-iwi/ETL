import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from data_sync_etl.adapters.concept_sources import conceptnet, wikidata
from data_sync_etl.domain.concepts import Dictionary, ExpansionPolicy, Snapshot, default_dictionary
from data_sync_etl.domain.lexical import ANALYZER_VERSION, analyze, bm25, lexical_document
from scripts.concept_dictionary import publish
from tests.unit.test_search import searchable as searchable


@pytest.mark.parametrize('query,expected', [
    ('học máy', 'machine learning'), ('máy học', 'machine learning'),
    ('database', 'cơ sở dữ liệu'), ('cơ sở dữ liệu', 'database'),
    ('xử lý ngôn ngữ tự nhiên', 'natural language processing'),
    ('bank account', 'ngân hàng'), ('river bank', 'bờ sông'),
    ('computer network', 'mạng máy tính'), ('neural network', 'mạng nơ ron'),
])
def test_reviewed_translation_and_sense_selection(query, expected):
    expansion = default_dictionary().expand(query)
    assert expected in {t.term for t in expansion.terms}
    assert all(t.sources and t.reason for t in expansion.terms)


@pytest.mark.parametrize('query', ['bank', 'ML', 'AI', 'NLP'])
def test_ambiguous_or_context_free_abbreviation_abstains(query):
    expansion = default_dictionary().expand(query)
    assert not expansion.terms
    assert expansion.recognized[0].concept_id is None


def test_domains_and_context_choose_abbreviations_without_cross_sense_drift():
    dictionary = default_dictionary()
    ml = dictionary.expand('ML model')
    assert 'machine learning' in {t.term for t in ml.terms}
    assert 'maximum likelihood' not in {t.term for t in ml.terms}
    stats = dictionary.expand('ML', domain='statistics')
    assert 'maximum likelihood' in {t.term for t in stats.terms}
    assert 'machine learning' not in {t.term for t in stats.terms}
    tied = dictionary.expand('ML model likelihood')
    assert not tied.terms
    assert 'natural language processing' in {t.term for t in dictionary.expand('NLP text').terms}


def test_relation_policy_depth_cycle_review_and_bounds():
    snapshot = default_dictionary().snapshot.model_copy(deep=True)
    ml = next(c for c in snapshot.concepts if c.concept_id == 'etl:machine_learning')
    ai = next(c for c in snapshot.concepts if c.concept_id == 'etl:artificial_intelligence')
    ai.relations.append(ml.relations[0].model_copy(update={'source_id': ai.concept_id, 'target_id': ml.concept_id}))
    dictionary = Dictionary(snapshot)
    assert 'artificial intelligence' not in {t.term for t in dictionary.expand('học máy').terms}
    policy = ExpansionPolicy(allowed_relations=['broader'], max_depth=3, max_per_concept=50)
    expanded = dictionary.expand('học máy', policy=policy)
    assert 'artificial intelligence' in {t.term for t in expanded.terms}
    assert len({(t.term, t.language, t.concept_id) for t in expanded.terms}) == len(expanded.terms)
    bounded = dictionary.expand('học máy database', policy=ExpansionPolicy(max_per_concept=2, max_total=3))
    assert sum(t.relation != 'original' for t in bounded.terms) == 3
    assert len(dictionary.expand('học máy', policy=ExpansionPolicy(max_depth=0)).terms) > 1
    direct = dictionary.expand('học máy artificial intelligence', policy=policy)
    assert next(t for t in direct.terms if t.term == 'artificial intelligence').relation == 'original'
    assert len({(t.term, t.language, t.concept_id) for t in direct.terms}) == len(direct.terms)
    ml.review_status = 'pending'
    assert not Dictionary(snapshot).expand('học máy').terms


def test_no_alias_or_query_repetition_boost_and_no_document_rewriting():
    docs = {'en': lexical_document(['machine learning']), 'vi': lexical_document(['học máy'])}
    before = deepcopy(docs)
    dictionary = default_dictionary()
    expected = bm25('học máy', docs)
    assert expected == bm25('học máy học máy học máy', docs)
    snapshot = dictionary.snapshot.model_copy(deep=True)
    concept = next(c for c in snapshot.concepts if c.concept_id == 'etl:machine_learning')
    concept.terms.extend(deepcopy(concept.terms) * 3)
    assert expected == bm25('học máy', docs, dictionary=Dictionary(snapshot))
    assert docs == before
    assert 'machine' not in docs['vi'].terms
    assert expected['vi'] > expected['en'] > 0


def test_underthesea_pinned_and_technical_versions_and_no_fallback(monkeypatch):
    import underthesea

    assert ANALYZER_VERSION.startswith('underthesea-8.3.0')
    assert {'c++', 'c#', '.net', 'python3', '3.12', 'không'} <= set(analyze('C++ C# .NET Python3 3.12 không'))
    monkeypatch.setattr(underthesea, 'word_tokenize', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline failure')))
    with pytest.raises(Exception, match='BLOCKED_LEXICAL_ANALYZER'):
        analyze('học máy')


def test_negation_longest_phrases_and_passage_boundaries():
    dictionary = default_dictionary()
    for query, wrong in [('học không giám sát', 'supervised learning'), ('học có giám sát', 'unsupervised learning')]:
        assert wrong not in {t.term for t in dictionary.expand(query).terms}
        assert bm25(query, {'wrong': lexical_document([wrong])}) == {}
    assert bm25('học máy', {'split': lexical_document(['machine', 'learning'])}) == {}


@pytest.mark.parametrize('searchable', [{'meanings': True, 'texts': [
    'Source text: Machine learning predicts labels from training data.'
]}], indirect=True)
def test_real_bow_path_with_meanings_enabled_returns_translation_evidence(searchable):
    c, _ = searchable
    c.indexing.embedding.prepare = lambda: pytest.fail('BoW loaded embedding')
    c.indexing.embedding.embed = lambda *_: pytest.fail('BoW encoded query')
    result = c.search.search('học máy', 'bow')
    assert result['total'] == 1
    evidence = result['items'][0]['lexical_evidence']
    row = next(e for e in evidence if e['relation'] == 'translation')
    assert row['query_phrase'] == 'học máy'
    assert row['matched_phrase'] == 'Machine learning'
    assert row['concept_id'] == 'etl:machine_learning'
    assert row['field'] == 'content'
    assert 'Machine learning' in result['items'][0]['snippet']
    assert result['query_expansion']['terms']
    assert result['search_fingerprint']
    from data_sync_etl.api.search_schemas import SearchResponse

    SearchResponse.model_validate(result)


def test_snapshot_validation_and_external_data_pending_curated_wins():
    raw = {'entities': {'Q1': {'labels': {'en': {'value': 'example'}, 'vi': {'value': 'ví dụ'}},
                               'aliases': {'en': [{'value': 'sample'}]},
                               'claims': {'P279': [{'mainsnak': {'datavalue': {'value': {'id': 'Q2'}}}}]}}}}
    snapshot, skipped = wikidata(raw, domain='test', version='candidate')
    assert skipped
    assert not Dictionary(snapshot).expand('example').terms
    assert all(t.review_status == 'pending' for t in snapshot.concepts[0].terms)
    base = default_dictionary().snapshot
    candidate = base.model_copy(deep=True)
    candidate.concepts[0].definition = 'conflicting external claim'
    with pytest.raises(ValueError, match='Conflict'):
        publish(base, candidate, 'new')
    duplicate = base.concepts[0].model_copy(deep=True)
    duplicate.concept_id = 'wd:Q123'
    duplicate.relations = []
    for term in duplicate.terms:
        term.concept_id = duplicate.concept_id
    with pytest.raises(ValueError, match='Alias conflict'):
        publish(base, Snapshot(version='external', concepts=[duplicate]), 'new')
    data = snapshot.model_dump()
    data['concepts'][0]['terms'][0]['normalized'] = 'bad'
    with pytest.raises(ValidationError):
        Snapshot.model_validate(data)


def test_conceptnet_relations_are_distinct_not_aliases():
    edges = []
    for kind in ('Synonym', 'IsA', 'RelatedTo', 'Antonym'):
        edges.append({'@id': 'edge:' + kind, 'rel': {'@id': '/r/' + kind},
            'start': {'@id': '/c/en/car', 'language': 'en', 'label': 'car'},
            'end': {'@id': '/c/vi/xe', 'language': 'vi', 'label': 'xe'}, 'license': 'cc:by-sa/4.0'})
    snapshot, skipped = conceptnet({'edges': edges}, domain='transport', version='candidate')
    assert len(skipped) == 1
    assert {r.kind for c in snapshot.concepts for r in c.relations} == {'translation', 'broader', 'related'}
    assert not any(r.allow_expansion for c in snapshot.concepts for r in c.relations)
    assert not Dictionary(snapshot).expand('car').terms


def test_new_manifest_and_old_migrations_unchanged_contracts():
    from data_sync_etl.adapters.mongo.lexical_schema import lexical_contract
    from data_sync_etl.adapters.mongo.meaning_schema import meaning_contract
    from data_sync_etl.domain.indexing import digest

    manifest = json.loads(Path('migrations_mongo/lexical_v2.json').read_text())
    assert manifest['lexical_validator_sha256'] == digest(lexical_contract(384))
    assert manifest['meaning_validator_sha256'] == digest(meaning_contract(384))


def test_dictionary_or_policy_changes_do_not_change_document_index_fingerprint():
    doc = lexical_document(['học máy'])
    snapshot = default_dictionary().snapshot.model_copy(deep=True)
    old = snapshot.fingerprint
    snapshot.version = 'new'
    assert snapshot.fingerprint != old
    one = default_dictionary().expand('học máy')
    two = default_dictionary().expand('học máy', policy=ExpansionPolicy(translations=0.5))
    assert one.policy_fingerprint != two.policy_fingerprint
    assert doc == lexical_document(['học máy'])


@pytest.mark.parametrize('searchable', [{'rejected_leaf': True, 'texts': [
    'Machine learning INTERNAL_DOCUMENT_SECRET'
]}], indirect=True)
def test_denied_document_never_contributes_evidence_or_snippet(searchable):
    c, _ = searchable
    result = c.search.search('học máy', 'bow')
    assert result['items'] == []
    assert 'INTERNAL_DOCUMENT_SECRET' not in json.dumps(result)


def test_document_sense_cannot_be_falsely_attributed_to_selected_query_sense():
    dictionary = default_dictionary()
    expansion = dictionary.expand('bank account')
    evidence = dictionary.evidence(expansion, 'The river bank is eroded.')
    assert not evidence
    scores = bm25('bank', {'river': lexical_document(['river bank']),
                           'finance': lexical_document(['bank account'])}, domain='finance')
    assert 'finance' in scores and 'river' not in scores


def test_cli_import_review_publish_roundtrip_is_offline_and_no_overwrite(tmp_path):
    import subprocess
    import sys

    source = tmp_path / 'source.json'
    source.write_text(json.dumps({'entities': {'Q123': {'labels': {'en': {'value': 'example'}},
        'aliases': {'en': [{'value': 'sample'}]}}}}), encoding='utf-8')
    draft, reviewed, released = (tmp_path / name for name in ('draft.json', 'reviewed.json', 'released.json'))
    command = [sys.executable, 'scripts/concept_dictionary.py']
    for args in [
        ['import-wikidata', str(source), str(draft), '--domain', 'test', '--version', 'draft'],
        ['validate', str(draft)],
        ['review', str(draft), str(reviewed), '--concept-id', 'wd:Q123', '--status', 'approved', '--reviewer', 'test-reviewer'],
        ['publish', str(reviewed), str(released), '--base', 'src/data_sync_etl/domain/data/concepts.json', '--version', 'test-release'],
    ]:
        subprocess.run([*command, *args], check=True, capture_output=True, text=True)
    published = Snapshot.model_validate_json(released.read_text(encoding='utf-8'))
    assert 'sample' in {t.term for t in Dictionary(published).expand('example').terms}
    before = released.read_bytes()
    result = subprocess.run([*command, 'schema', str(released)], capture_output=True, text=True)
    assert result.returncode != 0 and released.read_bytes() == before
