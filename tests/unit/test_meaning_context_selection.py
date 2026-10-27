import math

from data_sync_etl.domain.concepts import ExpansionPolicy, empty_dictionary
from data_sync_etl.domain.lexical import query_terms
from data_sync_etl.domain.meaning_bags import build_meanings, match_meaning
from tests.corpus_dictionary_support import reviewed_dictionary


class NearTieModel:
    model_id, version, dimension = 'context-test', 'v1', 3

    def __init__(self, scores):
        self.scores = scores

    def embed(self, text):
        value = self.scores[text]
        return [value, math.sqrt(1 - value * value), 0.]


def context(query, dictionary=None):
    dictionary, policy = dictionary or empty_dictionary(), ExpansionPolicy()
    return {'query': query, 'keywords': query_terms(query), 'dictionary': dictionary,
            'policy': policy, 'expansion': dictionary.expand(query, policy=policy)}


def test_near_tie_prefers_qualified_translation_with_original_source_offsets(tmp_path):
    parts = ['A learning process.', 'Học không giám sát dùng dữ liệu không nhãn.']
    bag = build_meanings([('leaf', '\n'.join(parts))], 'root', None,
        NearTieModel(dict(zip(parts, [.8, .79], strict=True))), .99)
    assert match_meaning([1., 0., 0.], bag)[1].text == parts[0]
    with reviewed_dictionary(tmp_path, 'vi: học không giám sát | en: unsupervised learning', 'unsupervised learning') as dictionary:
        score, member, _ = match_meaning([1., 0., 0.], bag, context=context('unsupervised learning', dictionary))
    assert member.text == parts[1]
    assert '\n'.join(parts)[member.start:member.end] == member.text
    assert abs(score - .8) < 1e-12  # Bag relevance is independent of excerpt selection.
    assert match_meaning([1., 0., 0.], bag,
        context={**context('unsupervised learning'), 'margin': 0})[1].text == parts[0]


def test_literal_words_cannot_promote_a_semantically_distant_member():
    parts = ['An unrelated exact phrase about unsupervised learning.', 'Semantic source.']
    bag = build_meanings([('leaf', '\n'.join(parts))], 'root', None,
        NearTieModel(dict(zip(parts, [.1, .8], strict=True))), .99)
    assert match_meaning([1., 0., 0.], bag, context=context('unsupervised learning'))[1].text == parts[1]
