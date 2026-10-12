from typing import Protocol

from data_sync_etl.domain.subject import Subject


class SubjectReader(Protocol):
    def list_subjects(
        self,
        *,
        keyword: str | None,
        grade_level_id: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[Subject], int]: ...

    def list_recommended_subjects(
        self,
        *,
        grade_level_id: str | None,
        excluded_ids: set[str],
    ) -> list[Subject]: ...
