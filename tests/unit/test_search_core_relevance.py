"""Synthetic relevance regressions; no live database/model/network required."""
import math

import pytest

from data_sync_etl.domain.concepts import ExpansionPolicy, default_dictionary
from data_sync_etl.domain.lexical import bm25, lexical_document
from data_sync_etl.domain.query_variants import approved_accent_variant
from data_sync_etl.domain.vector_admission import anchored_dense_match
from tests.unit.test_metadata_search import metadata as metadata
from tests.unit.test_metadata_search import search
from tests.unit.test_search import searchable as searchable


def test_missing_accents_require_fraction_concept_not_incidental_number():
    query = 'Cong hai phan so khac mau'
    dictionary = default_dictionary()
    variant = approved_accent_variant(query, dictionary, policy=ExpansionPolicy())
    docs = {'security': lexical_document(['Xác thực hai yếu tố bảo vệ tài khoản.']),
            'math': lexical_document(['Cộng hai phân số khác mẫu cần quy đồng.'])}
    raw = bm25(query, docs, focus_query=variant)
    assisted = bm25(variant, docs)
    assert 'security' not in raw and 'security' not in assisted
    assert assisted['math'] > 0


def test_known_subject_cannot_be_replaced_by_leftover_qualifier():
    docs = {'security': lexical_document(['Hai yếu tố khác trong bảo mật.']),
            'math': lexical_document(['Cộng phân số khác mẫu.'])}
    assert set(bm25('cộng hai phân số khác mẫu', docs)) == {'math'}
    # A rare informative literal must still work without curated recognition.
    assert bm25('unknown1 unknown2 xylophone', {'music': lexical_document(['xylophone'])})
    assert bm25('hai', {'number': lexical_document(['hai'])})


def test_metadata_and_content_use_same_core_policy_and_accent_variant(metadata):
    c, _, extension, service = metadata
    c.settings.search_approved_accent_variants_enabled = True
    service.reconcile(apply=True)
    assert search(c, 'Cong hai phan so khac mau')['total'] == 1
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'title': 'Xác thực hai yếu tố bảo mật'})
    extension.description, extension.tags = None, []
    service.reconcile(apply=True)
    assert search(c, 'Cong hai phan so khac mau')['total'] == 0


def test_metadata_respects_configured_literal_coverage(metadata):
    c, _, extension, service = metadata
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'title': 'xylophone'})
    extension.description, extension.tags = None, []
    c.settings.search_bow_min_coverage = .75
    service.reconcile(apply=True)
    assert search(c, 'unknown1 unknown2 xylophone')['total'] == 0


def test_metadata_dense_uses_same_verified_source_admission(metadata, monkeypatch):
    c, _, extension, service = metadata
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'title': 'Cư dân Văn Lang trồng lúa nước'})
    extension.description, extension.tags = None, []
    service.reconcile(apply=True, dense=True)
    vector = [.49, math.sqrt(1 - .49 ** 2), *([0.] * 1022)]
    monkeypatch.setattr(service.embedding, 'embed_queries', lambda _: [{'vector': vector}])
    c.settings.search_vector_anchor_fallback_enabled = False
    assert search(c, 'Van Lang', method='vector')['total'] == 0
    c.settings.search_vector_anchor_fallback_enabled = True
    assert search(c, 'Van Lang', method='vector')['total'] == 1
    assert search(c, 'unrelated astronomical quantum', method='vector')['total'] == 0


@pytest.mark.parametrize('query', ['Van Lang', 'Văn Lang', 'văn lang',
    'How was the life of the Van Lang inhabitants connected to wet-rice cultivation and bronze casting?'])
def test_subthreshold_dense_hit_requires_verified_multitoken_anchor(query):
    assert anchored_dense_match(query, 'Cư dân Văn Lang trồng lúa nước và đúc đồng.', .49, floor=.4)
    assert not anchored_dense_match(query, 'Xác thực hai yếu tố để bảo mật tài khoản.', .49, floor=.4)
    assert not anchored_dense_match(query, 'Cư dân Văn Lang trồng lúa nước.', .39, floor=.4)
    assert not anchored_dense_match(query, 'Văn. Lang là một từ ở đoạn khác.', .49, floor=.4)


def test_generic_or_single_word_overlap_never_satisfies_dense_anchor():
    assert not anchored_dense_match('How was life connected?', 'life', .5, floor=.4)
    assert not anchored_dense_match('Lang', 'Văn Lang', .5, floor=.4)
    assert not anchored_dense_match('missing subject', 'missing unrelated subject', .5, floor=.4)


def test_approved_cross_language_core_can_support_dense_hit_without_literal_alias():
    dictionary, policy = default_dictionary(), ExpansionPolicy()
    expansion = dictionary.expand('machine learning', policy=policy)
    options = dict(floor=.4, dictionary=dictionary, policy=policy, expansion=expansion)
    assert anchored_dense_match('machine learning', 'Tài liệu về học máy.', .49, **options)
    assert not anchored_dense_match('machine learning', 'Tài liệu về máy tính.', .49, **options)
    expansion = dictionary.expand('machine learning and two', policy=policy)
    assert not anchored_dense_match('machine learning and two', 'two factor security', .49,
        floor=.4, dictionary=dictionary, policy=policy, expansion=expansion)


@pytest.mark.parametrize('searchable', [
    {'texts': ['The Van Lang inhabitants grew wet rice and cast bronze tools.'], 'meanings': False},
    {'texts': ['The Van Lang inhabitants grew wet rice and cast bronze tools.'], 'meanings': True},
], indirect=True)
def test_vector_admission_uses_real_verified_passage_and_keeps_dense_score(searchable, monkeypatch):
    c, _ = searchable
    # Fixture vectors are identical. Construct an orthogonal query whose actual
    # cosine is .49, without changing any persisted vector or index fingerprint.
    unit = [v / math.sqrt(.14) for v in [.1, .2, .3]]
    orthogonal = [unit[1], -unit[0], 0.]
    norm = math.hypot(*orthogonal)
    query_vector = [.49 * a + math.sqrt(1 - .49 ** 2) * b / norm
                    for a, b in zip(unit, orthogonal, strict=True)]
    monkeypatch.setattr(c.indexing.embedding, 'embed', lambda _: query_vector)
    c.settings.search_vector_min_similarity = .6
    c.settings.search_meaning_context_fallback_enabled = False
    c.settings.search_vector_anchor_fallback_enabled = False
    assert c.search.search('Van Lang', 'vector')['total'] == 0
    c.settings.search_vector_anchor_fallback_enabled = True
    for query in ['Van Lang', 'Văn Lang',
                  'How was the life of the Van Lang inhabitants connected to wet-rice cultivation and bronze casting?']:
        result = c.search.search(query, 'vector')
        assert result['total'] == 1
        assert result['items'][0]['score_components']['vector'] == pytest.approx(.49)
        assert 'Van Lang' in result['items'][0]['snippet']
    assert c.search.search('unrelated astronomical quantum', 'vector')['total'] == 0
