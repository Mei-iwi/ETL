from sqlalchemy import func, or_, select

from data_sync_etl.db.models import MasterLearningResource, ResourceSubject, Subject
from data_sync_etl.domain.subject import Subject as SubjectDTO


class SQLAlchemySubjectReader:
    """SQLAlchemy adapter implementing the subject read port."""

    def __init__(self, session_factory):
        self._session_factory = session_factory

    @staticmethod
    def _active_conditions():
        return (Subject.is_active.is_(True), Subject.deleted_at.is_(None))

    @staticmethod
    def _to_domain(row, resource_count: int = 0) -> SubjectDTO:
        return SubjectDTO(
            id=row.id,
            name=row.subject_name,
            slug=row.subject_slug,
            grade_level_id=row.grade_id,
            resource_count=int(resource_count or 0),
        )

    def list_subjects(
        self,
        *,
        keyword: str | None,
        grade_level_id: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[SubjectDTO], int]:
        with self._session_factory() as session:
            conditions = list(self._active_conditions())
            if grade_level_id:
                conditions.append(Subject.grade_id == grade_level_id)
            if keyword:
                escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pattern = f"%{escaped}%"
                conditions.append(
                    or_(
                        Subject.subject_name.ilike(pattern, escape="\\"),
                        Subject.subject_slug.ilike(pattern, escape="\\"),
                    )
                )

            total = (
                session.scalar(select(func.count()).select_from(Subject).where(*conditions)) or 0
            )
            stmt = (
                select(Subject)
                .where(*conditions)
                .order_by(Subject.subject_name.asc(), Subject.id.asc())
                .offset(offset)
                .limit(limit)
            )
            rows = session.scalars(stmt).all()
            return [self._to_domain(row) for row in rows], int(total)

    def list_recommended_subjects(
        self,
        *,
        grade_level_id: str | None,
        excluded_ids: set[str],
    ) -> list[SubjectDTO]:
        with self._session_factory() as session:
            resource_counts = (
                select(
                    ResourceSubject.subject_id,
                    func.count(ResourceSubject.resource_id).label("resource_count"),
                )
                .join(
                    MasterLearningResource,
                    MasterLearningResource.id == ResourceSubject.resource_id,
                )
                .where(
                    MasterLearningResource.is_active.is_(True),
                    MasterLearningResource.deleted_at.is_(None),
                    MasterLearningResource.publication_status == "PUBLISHED",
                )
                .group_by(ResourceSubject.subject_id)
                .subquery()
            )
            conditions = list(self._active_conditions())
            if excluded_ids:
                conditions.append(Subject.id.not_in(excluded_ids))

            count_column = func.coalesce(resource_counts.c.resource_count, 0)
            stmt = (
                select(Subject, count_column)
                .outerjoin(resource_counts, resource_counts.c.subject_id == Subject.id)
                .where(*conditions)
                .order_by(count_column.desc(), Subject.subject_name.asc(), Subject.id.asc())
            )
            rows = session.execute(stmt).all()
            return [self._to_domain(subject, count) for subject, count in rows]
