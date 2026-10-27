"""Literal search is independent of static files and unpublished terminology."""
from pathlib import Path

import pytest

from data_sync_etl.application.concept_resolver import ConceptDictionaryProvider
from data_sync_etl.domain.concepts import empty_dictionary
from data_sync_etl.domain.lexical import bm25, lexical_document


def test_empty_fallback_never_reads_a_snapshot(monkeypatch):
    monkeypatch.setattr(Path, 'read_text', lambda *a, **k: pytest.fail('Unexpected dictionary IO'))
    dictionary = empty_dictionary()
    assert dictionary.concepts == {}
    assert dictionary.expand('photosynthesis quang hợp').terms == []
    assert dictionary.expand('raw original').original == 'raw original'


def test_raw_bm25_uses_document_tf_without_dictionary_authority():
    docs = {'one': lexical_document(['zirconium spectroscopy']),
            'two': lexical_document(['unrelated content'])}
    assert set(bm25('zirconium', docs)) == {'one'}
    assert bm25('translation not present', docs) == {}


def test_unverified_runtime_never_loads_or_falls_back_to_static_aliases():
    provider = ConceptDictionaryProvider(enabled=True)
    assert provider.refresh()['status'] == 'BLOCKED_LIVE'
    with provider.pin('photosynthesis') as dictionary:
        assert dictionary is empty_dictionary()
        assert not dictionary.expand('photosynthesis').terms


def test_search_startup_ignores_legacy_snapshot_path(tmp_path, monkeypatch):
    from data_sync_etl.application.search import SearchService
    from data_sync_etl.config import Settings

    settings = Settings(_env_file=None, search_concept_snapshot=tmp_path / 'missing-static.json')
    monkeypatch.setattr(Path, 'read_text', lambda *a, **k: pytest.fail('Legacy snapshot was read'))
    assert SearchService(None, settings, None).dictionary is empty_dictionary()
