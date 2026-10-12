from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Subject:
    id: str
    name: str
    slug: str
    grade_level_id: str | None
    resource_count: int = 0


@dataclass(frozen=True, slots=True)
class RecommendedSubject:
    id: str
    name: str
    slug: str
    grade_level_id: str | None
    score: int
    reason: str


class SubjectRecommendationPolicy(Protocol):
    def rank(
        self,
        subjects: list[Subject],
        *,
        grade_level_id: str | None,
        limit: int,
    ) -> list[RecommendedSubject]: ...


class GradeBasedSubjectRecommendationPolicy:
    def rank(
        self,
        subjects: list[Subject],
        *,
        grade_level_id: str | None,
        limit: int,
    ) -> list[RecommendedSubject]:
        def sort_key(subject: Subject) -> tuple[int, int, str, str]:
            same_grade = bool(grade_level_id and subject.grade_level_id == grade_level_id)
            return (
                0 if same_grade else 1,
                -subject.resource_count,
                subject.name.casefold(),
                subject.id,
            )

        result: list[RecommendedSubject] = []
        for subject in sorted(subjects, key=sort_key)[:limit]:
            same_grade = bool(grade_level_id and subject.grade_level_id == grade_level_id)
            if same_grade:
                score, reason = 95, "Phù hợp với khối lớp"
            elif subject.resource_count:
                score, reason = 70, "Có nhiều học liệu liên kết"
            else:
                score, reason = 50, "Môn học đang hoạt động"
            result.append(
                RecommendedSubject(
                    id=subject.id,
                    name=subject.name,
                    slug=subject.slug,
                    grade_level_id=subject.grade_level_id,
                    score=score,
                    reason=reason,
                )
            )
        return result
