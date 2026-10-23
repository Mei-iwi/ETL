import math
from copy import deepcopy

import pytest
from pydantic import ValidationError

from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.meaning_bags import (
    MeaningBag,
    MeaningMember,
    build_meanings,
    cluster,
    match_meaning,
    sources,
    verify_meanings,
)
from tests.unit.test_search import searchable as searchable


class MultilingualDouble:
    """Plumbing only; the offline model script separately checks real quality."""

    model_id, version, dimension = 'test-meaning-only', 'v1', 3

    def __init__(self):
        self.calls = []

    def embed(self, text):
        self.calls.append(text)
        return [0., 1., 0.] if 'binary' in text.casefold() else [1., 0., 0.]


def test_groups_observed_languages_and_descriptions_with_source_evidence():
    units = [('a', 'Học máy\nMachine learning\nMáy học\nHệ thống học quy luật từ dữ liệu'),
             ('b', 'binary search')]
    model = MultilingualDouble()
    bag = build_meanings(units, 'root', None, model, 0.8)
    assert [len(group.members) for group in bag.groups] == [4, 1]
    assert len(model.calls) == 5
    for member in bag.members:
        assert dict(units)[member.unit_id][member.start:member.end] == member.text
    score, source, variants = match_meaning([1., 0., 0.], bag)
    assert score == pytest.approx(1)
    assert source.text == 'Học máy'
    assert 'Machine learning' in variants
    assert 'Hệ thống học quy luật từ dữ liệu' in variants
    assert 'binary search' not in variants
    assert MeaningBag.model_validate(bag.model_dump()) == bag


def test_complete_link_does_not_chain_merely_related_expressions():
    members = [MeaningMember(unit_id='a', kind='text', start=0, end=1, text=str(i),
                             embedding=[math.cos(angle), math.sin(angle), 0.])
               for i, angle in enumerate([0, math.pi / 6, math.pi / 3])]
    assert [group.members for group in cluster(members, .8)] == [[0, 1], [2]]


def test_source_unicode_offsets_long_passages_and_no_cross_leaf_fabrication():
    units = [('a', '  Học máy.\n機械学習 ' + '机器学习' * 600), ('b', 'learning')]
    observed = sources(units, 'root', 'Title')
    assert observed[0].kind == 'title'
    assert all(member.text == dict(units)[member.unit_id][member.start:member.end]
               for member in observed if member.kind == 'text')
    assert all(member.end - member.start <= 2000 for member in observed)
    assert sum(len(member.text) for member in observed if member.unit_id == 'a') >= 2400


def test_duplicate_occurrences_reuse_encoding_but_preserve_both_sources():
    model = MultilingualDouble()
    bag = build_meanings([('a', 'same expression'), ('b', 'same expression')], 'root', None, model, .8)
    assert model.calls == ['same expression']
    assert [member.unit_id for member in bag.members] == ['a', 'b']


def test_cjk_sentences_split_without_whitespace_between_meanings():
    observed = sources([('a', '机器学习用于预测。数据库存储记录！')], 'root', None)
    assert [source.text for source in observed] == ['机器学习用于预测。', '数据库存储记录！']


def test_limits_fail_before_encoding_instead_of_truncating():
    model = MultilingualDouble()
    with pytest.raises(IndexingError, match='MEANING_BAG_LIMIT_EXCEEDED'):
        build_meanings([('a', '\n'.join(f'expression {i}' for i in range(257)))], 'root', None, model, .8)
    assert model.calls == []


def test_source_model_threshold_and_payload_mutations_are_detected():
    model = MultilingualDouble()
    bag = build_meanings([('a', 'Học máy')], 'root', None, model, .8)
    assert verify_meanings(bag, [('a', 'Học máy')], 'root', None, 'test-meaning-only@v1', 3, .8)
    assert not verify_meanings(bag, [('a', 'Học sâu')], 'root', None, 'test-meaning-only@v1', 3, .8)
    assert not verify_meanings(bag, [('a', 'Học máy')], 'root', None, 'other@v1', 3, .8)
    assert not verify_meanings(bag, [('a', 'Học máy')], 'root', None, 'test-meaning-only@v1', 3, .9)
    raw = deepcopy(bag.model_dump())
    raw['members'][0]['embedding'] = [0., 1., 0.]
    with pytest.raises(ValidationError, match='checksum'):
        MeaningBag.model_validate(raw)


@pytest.mark.parametrize('searchable', [{'structured': True, 'texts': [
    'unrelated filler\n' * 15 + 'A xylophone produces musical sounds.',
]}], indirect=True)
def test_automatic_meaning_reindex_search_and_semantic_context(searchable, monkeypatch):
    c, version = searchable
    old = deepcopy(c.indexing.bags.rows)
    c.settings.index_meaning_bags_enabled = True
    assert c.indexing.status(version['id'])['index_status'] == 'STALE'

    def embed(text):
        return [1., 0., 0.] if 'xylophone' in text or text == 'foreignquery' else [0., 1., 0.]

    c.indexing.embedding.embed = embed
    c.indexing.enqueue(version['id'], force=True)
    assert c.indexing.once()
    assert c.indexing.status(version['id'])['index_status'] == 'READY'
    assert all(bag.meanings is not None for bag in c.indexing.bags.rows.values())
    assert {key: row.lexical for key, row in old.items()} == {
        key: row.lexical for key, row in c.indexing.bags.rows.items()}
    monkeypatch.setattr('data_sync_etl.application.search.cosine',
                        lambda *_: pytest.fail('Search used the legacy keyword vector'))
    for method in ['vector', 'hybrid']:
        item = c.search.search('foreignquery', method)['items'][0]
        assert item['context_evidence'] == 'semantic'
        assert 'xylophone' in item['snippet']
        assert any('xylophone' in value for value in item['meaning_variants'])
        selected = c.search.context(item['context_id'], neighbors=0)['chunks'][0]
        assert item['snippet'] in selected['text']
        assert selected['unit_chunk_sequence'] > 1
