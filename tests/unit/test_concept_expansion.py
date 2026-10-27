"""Corpus-reviewed expansion contracts; no static seed assumptions."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from data_sync_etl.adapters.concept_sources import conceptnet
from data_sync_etl.domain.concepts import Dictionary, ExpansionPolicy, empty_dictionary
from data_sync_etl.domain.lexical import ANALYZER_VERSION, analyze, bm25, lexical_document
from tests.corpus_dictionary_support import reviewed_dictionary
from tests.unit.test_search import searchable as searchable


def test_underthesea_pinned_and_technical_versions_and_no_fallback(monkeypatch):
    import underthesea

    assert ANALYZER_VERSION.startswith('underthesea-8.3.0')
    assert {'c++', 'c#', '.net', 'python3', '3.12', 'không'} <= set(analyze('C++ C# .NET Python3 3.12 không'))
    monkeypatch.setattr(underthesea, 'word_tokenize', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline failure')))
    with pytest.raises(Exception, match='BLOCKED_LEXICAL_ANALYZER'):
        analyze('học máy')


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
    snapshot = empty_dictionary().snapshot.model_copy(deep=True)
    old = snapshot.fingerprint
    snapshot.version = 'new'
    assert snapshot.fingerprint != old
    one = empty_dictionary().expand('học máy')
    two = empty_dictionary().expand('học máy', policy=ExpansionPolicy(translations=0.5))
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


def test_reviewed_corpus_translation_has_real_provenance_and_no_tf_rewriting(tmp_path):
    docs = {'fr': lexical_document(['réseau cristallin']), 'en': lexical_document(['zircon lattice'])}
    before = deepcopy(docs)
    assert bm25('zircon lattice', {'fr': docs['fr']}) == {}
    with reviewed_dictionary(tmp_path, 'en: zircon lattice | fr: réseau cristallin', 'zircon lattice') as dictionary:
        expansion = dictionary.expand('zircon lattice')
        assert all(t.sources and t.reason for t in expansion.terms)
        assert 'réseau cristallin' in {t.term for t in expansion.terms}
        ranking = bm25('zircon lattice', docs, dictionary=dictionary)
        assert ranking['en'] > ranking['fr'] > 0
        assert ranking == bm25('zircon lattice zircon lattice', docs, dictionary=dictionary)
        assert docs == before
        snapshot = dictionary.snapshot.model_copy(deep=True)
        snapshot.concepts[0].terms.extend(deepcopy(snapshot.concepts[0].terms))
        assert ranking == bm25('zircon lattice', docs, dictionary=Dictionary(snapshot))


def test_pending_terms_and_disabled_expansion_cannot_translate(tmp_path):
    with reviewed_dictionary(tmp_path, 'en: zircon lattice | fr: réseau cristallin', 'zircon lattice') as dictionary:
        snapshot = dictionary.snapshot.model_copy(deep=True)
        for term in snapshot.concepts[0].terms:
            term.review_status = 'pending'
        assert not Dictionary(snapshot).expand('zircon lattice').terms
        assert not dictionary.expand('zircon lattice', policy=ExpansionPolicy(enabled=False)).terms
        assert bm25('zircon lattice', {'split': lexical_document(['réseau', 'cristallin'])}, dictionary=dictionary) == {}


def test_ambiguous_corpus_senses_abstain_without_context(tmp_path):
    with reviewed_dictionary(tmp_path, 'en: zircon lattice | fr: réseau cristallin', 'zircon lattice') as dictionary:
        snapshot = dictionary.snapshot.model_copy(deep=True)
        other = snapshot.concepts[0].model_copy(deep=True)
        other.concept_id += ':other'
        for term in other.terms:
            term.concept_id = other.concept_id
        snapshot.concepts.append(other)
        assert not Dictionary(snapshot).expand('zircon lattice').terms
        assert empty_dictionary().expand('zircon lattice').terms == []
