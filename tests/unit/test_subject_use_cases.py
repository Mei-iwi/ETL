import pytest

from data_sync_etl.application.list_subjects_use_case import ListSubjectsUseCase
from data_sync_etl.application.recommend_subjects_use_case import RecommendSubjectsUseCase
from data_sync_etl.domain.subject import GradeBasedSubjectRecommendationPolicy, Subject


class FakeSubjectReader:
    def __init__(self):
        self.subjects = [
            Subject("s1", "Toán", "toan", "g1", 10),
            Subject("s2", "Văn", "van", "g2", 3),
        ]
        self.last_list_args = None
        self.last_recommend_args = None

    def list_subjects(self, *, keyword, grade_level_id, offset, limit):
        self.last_list_args = (keyword, grade_level_id, offset, limit)
        selected = self.subjects
        if keyword:
            selected = [s for s in selected if keyword.casefold() in s.name.casefold()]
        if grade_level_id:
            selected = [s for s in selected if s.grade_level_id == grade_level_id]
        return selected[offset : offset + limit], len(selected)

    def list_recommended_subjects(self, *, grade_level_id, excluded_ids):
        self.last_recommend_args = (grade_level_id, excluded_ids)
        return [s for s in self.subjects if s.id not in excluded_ids]


def test_list_subjects_trims_keyword_and_calculates_pagination():
    reader = FakeSubjectReader()
    result = ListSubjectsUseCase(reader).execute(page=2, size=1, keyword="  Toán  ")

    assert result["pagination"] == {"page": 2, "size": 1, "total": 1, "total_pages": 1}
    assert reader.last_list_args == ("Toán", None, 1, 1)


def test_list_subjects_rejects_invalid_pagination():
    use_case = ListSubjectsUseCase(FakeSubjectReader())
    with pytest.raises(ValueError):
        use_case.execute(page=0)
    with pytest.raises(ValueError):
        use_case.execute(size=101)


def test_recommendation_excludes_ids_and_returns_expected_shape():
    reader = FakeSubjectReader()
    use_case = RecommendSubjectsUseCase(reader, GradeBasedSubjectRecommendationPolicy())

    result = use_case.execute(grade_level_id="g1", limit=10, exclude_ids=" s2 ")

    assert [item["id"] for item in result["items"]] == ["s1"]
    assert reader.last_recommend_args == ("g1", {"s2"})


def test_recommendation_rejects_bad_exclude_ids_and_limit():
    use_case = RecommendSubjectsUseCase(
        FakeSubjectReader(), GradeBasedSubjectRecommendationPolicy()
    )
    with pytest.raises(ValueError):
        use_case.execute(limit=51)
    with pytest.raises(ValueError):
        use_case.execute(exclude_ids="s1,,s2")
