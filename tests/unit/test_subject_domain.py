from data_sync_etl.domain.subject import (
    GradeBasedSubjectRecommendationPolicy,
    Subject,
)


def test_policy_prioritizes_same_grade_then_popularity_then_name_and_id():
    policy = GradeBasedSubjectRecommendationPolicy()
    candidates = [
        Subject("z", "Lý", "ly", "grade-2", 100),
        Subject("b", "Văn", "van", "grade-1", 1),
        Subject("a", "Toán", "toan", "grade-1", 1),
        Subject("c", "Anh", "anh", "grade-2", 50),
    ]

    result = policy.rank(candidates, grade_level_id="grade-1", limit=10)

    assert [item.id for item in result] == ["a", "b", "z", "c"]
    assert result[0].score == 95
    assert result[0].reason == "Phù hợp với khối lớp"


def test_policy_is_deterministic_and_respects_limit():
    policy = GradeBasedSubjectRecommendationPolicy()
    candidates = [
        Subject("2", "Toán", "toan", None, 3),
        Subject("1", "Toán", "toan-1", None, 3),
    ]

    first = policy.rank(candidates, grade_level_id=None, limit=1)
    second = policy.rank(list(reversed(candidates)), grade_level_id=None, limit=1)

    assert first == second
    assert len(first) == 1
