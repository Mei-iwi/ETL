"""Unit tests for Query Understanding and Decomposition (QueryPlanner)."""

import pytest
from pydantic import ValidationError

from data_sync_etl.domain.query_planner import (
    QueryPlan,
    detect_language_hints,
    detect_technical_identifiers,
    plan_query,
)


def test_query_plan_immutability():
    plan = plan_query("lập trình python")
    assert isinstance(plan, QueryPlan)
    with pytest.raises(ValidationError):
        plan.canonical_query = "something else"


def test_technical_identifiers_preservation():
    query = "Học lập trình C++ và C# nâng cao, kèm công thức H2SO4 và API RESTful_v2"
    identifiers = detect_technical_identifiers(query)
    assert "C++" in identifiers
    assert "C#" in identifiers
    assert "H2SO4" in identifiers
    assert any("REST" in ident or "v2" in ident for ident in identifiers)

    plan = plan_query(query)
    assert "C++" in plan.technical_identifiers
    assert "C#" in plan.technical_identifiers
    assert "H2SO4" in plan.technical_identifiers


def test_negation_and_comparison_preservation():
    query = "so sánh NoSQL và SQL nhưng không dùng MongoDB mà dùng PostgreSQL"
    plan = plan_query(query)
    assert plan.query_form in ("comparison", "multi_clause")
    assert len(plan.negation_terms) > 0
    assert any("không" in term for term in plan.negation_terms)
    assert len(plan.comparison_constraints) > 0


def test_conversational_question_stripping():
    query = "cho tôi hỏi làm thế nào để giải phương trình vi phân bậc hai?"
    plan = plan_query(query)
    assert plan.query_form == "question"
    # The core query should focus on the actual subject
    assert "phương trình vi phân bậc hai" in plan.core_query
    # Conversational prefix should be removed from core_query
    assert not plan.core_query.startswith("cho tôi hỏi")


def test_language_detection_hints():
    assert "vi" in detect_language_hints("tài liệu giải tích và đại số tuyến tính")
    assert "en" in detect_language_hints("introduction to algorithms and data structures")
    assert "fr" in detect_language_hints("introduction aux algorithmes et structures de données")


def test_multi_clause_decomposition():
    query = "bài giảng đại số tuyến tính, ma trận nghịch đảo và định thức"
    plan = plan_query(query)
    assert len(plan.decomposed_subqueries) >= 2
    # Bounded to at most 4 subqueries
    assert len(plan.decomposed_subqueries) <= 4


def test_retrieval_variants_bounded():
    query = "tìm kiếm học liệu toán rời rạc và đồ thị"
    plan = plan_query(query)
    assert 1 <= len(plan.retrieval_variants) <= 4
    # Ensure variants include normalized representations
    assert any("toán" in v or "toan" in v for v in plan.retrieval_variants)


@pytest.mark.parametrize('text', ['quang hợp', 'quang hop', 'duong', 'C++ khác C# như thế nào?',
    'Không phải SQL mà là NoSQL', 'Tìm bài học về số âm', 'So sánh CO2 và CO₂',
    'How does photosynthesis work?', 'Comment fonctionne la photosynthèse ?',
    'Tìm tài liệu về machine learning', 'HL26_R013 H2SO4 .NET', 'doi song cu dan van lang'])
def test_representative_queries_keep_original_symbols_and_intent(text):
    from data_sync_etl.domain.search import bounded_query
    plan = plan_query(text)
    assert plan.retrieval_variants[0] == bounded_query(text)
    assert plan == plan_query(text)
    assert isinstance(plan.retrieval_variants, tuple)
    for term in plan.technical_identifiers:
        assert term in plan.core_query
    if plan.negation_terms or plan.comparison_constraints:
        assert not plan.decomposed_subqueries


def test_normalization_nfd_whitespace_and_nested_immutability():
    import unicodedata
    plan = plan_query('  Tìm\t tài liệu\n về C++ và C#  ')
    assert plan.canonical_query == 'Tìm tài liệu về C++ và C#'
    assert plan_query(unicodedata.normalize('NFD', plan.canonical_query)).fingerprint == plan.fingerprint
    with pytest.raises(AttributeError):
        plan.retrieval_variants.append('injected')
    assert plan_query('CO2').canonical_query != plan_query('CO₂').canonical_query


@pytest.mark.parametrize('text', ['', 'a' * 4001, 'x ' * 513, 'a\x00b', 'a\x1fb', '\ud800'])
def test_planner_rejects_over_budget_or_malformed_queries(text):
    with pytest.raises(ValueError):
        plan_query(text)


@pytest.mark.parametrize('query,required', [
    ('Hãy giúp tôi tìm tài liệu giải thích chi tiết về cách giải phương trình bậc hai.', 'cách'),
    ('Phương trình này có bao nhiêu nghiệm?', 'bao nhiêu'),
    ('Cách cộng hai phân số khác mẫu', 'khác mẫu'),
    ('Không phải SQL mà là NoSQL', 'Không phải SQL mà là NoSQL'),
])
def test_core_does_not_remove_method_quantity_or_negation(query, required):
    assert required in plan_query(query).core_query


def test_mandatory_conditions_and_quoted_phrases_are_not_split_into_or_queries():
    for query in ['Tìm tài liệu phải có quang hợp, ánh sáng và khí cacbonic trong cùng một bài',
                  'Tài liệu về "quang hợp ở thực vật", ánh sáng và vai trò của khí cacbonic']:
        assert plan_query(query).decomposed_subqueries == ()


def test_plans_are_isolated_across_concurrent_requests():
    from concurrent.futures import ThreadPoolExecutor
    queries = ['C++ khác C#', 'quang hợp', 'How does photosynthesis work?', 'không phải SQL'] * 8
    with ThreadPoolExecutor(max_workers=4) as pool:
        plans = list(pool.map(plan_query, queries))
    assert [plan.canonical_query for plan in plans] == queries
    assert all(plan == plan_query(query) for query, plan in zip(queries, plans, strict=True))


def test_punctuation_only_difference_does_not_spend_an_extra_retrieval_branch():
    from data_sync_etl.domain.query_planner import lexical_plan_variants
    plan = plan_query('How does photosynthesis work?')
    assert lexical_plan_variants(plan) == (plan.canonical_query,)
    assert plan.retrieval_variants == (plan.canonical_query,)
