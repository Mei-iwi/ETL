from data_sync_etl.domain.subject import SubjectRecommendationPolicy
from data_sync_etl.ports.subject_reader import SubjectReader


class RecommendSubjectsUseCase:
    def __init__(self, reader: SubjectReader, policy: SubjectRecommendationPolicy):
        self._reader = reader
        self._policy = policy

    def execute(
        self,
        *,
        grade_level_id: str | None = None,
        limit: int = 10,
        exclude_ids: str | None = None,
    ) -> dict:
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")

        grade_level_id = grade_level_id.strip() if grade_level_id else None
        grade_level_id = grade_level_id or None

        excluded_ids: set[str] = set()
        if exclude_ids:
            raw_ids = exclude_ids.split(",")
            if any(not value.strip() for value in raw_ids):
                raise ValueError("exclude_ids must be comma-separated non-empty IDs")
            excluded_ids = {value.strip() for value in raw_ids}
            if any(len(value) > 30 for value in excluded_ids):
                raise ValueError("each excluded ID must be at most 30 characters")

        candidates = self._reader.list_recommended_subjects(
            grade_level_id=grade_level_id,
            excluded_ids=excluded_ids,
        )
        ranked = self._policy.rank(
            candidates,
            grade_level_id=grade_level_id,
            limit=limit,
        )
        return {
            "items": [
                {
                    "id": item.id,
                    "name": item.name,
                    "slug": item.slug,
                    "grade_level_id": item.grade_level_id,
                    "score": item.score,
                    "reason": item.reason,
                }
                for item in ranked
            ]
        }
