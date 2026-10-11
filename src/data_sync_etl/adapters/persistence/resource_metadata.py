from datetime import UTC, datetime

from sqlalchemy import delete, select

from data_sync_etl.db.models import MasterLearningResource, ResourceGradeLevel, ResourceSubject


class ResourceMetadataRepositoryMixin:
    def get_resource_for_update(self, resource_id):
        row = self.session.execute(
            select(MasterLearningResource)
            .where(MasterLearningResource.id == resource_id)
            .with_for_update()
        ).scalar_one_or_none()
        return self._dict(row) if row else None

    def update_resource_metadata(self, resource_id, values):
        mapping = {
            "title": "title",
            "resource_type_code": "resource_type_code",
            "resource_type_name_vi": "resource_type_name_vi",
            "provider_id": "provider_id",
            "provider_name": "provider_name",
            "provider_type": "provider_type",
            "publication_status": "publication_status",
            "uploaded_by": "created_by",
        }
        row = self.session.get(MasterLearningResource, resource_id)
        if row is None:
            raise LookupError("Resource not found")
        for source, target in mapping.items():
            if source in values:
                setattr(row, target, values[source])
        row.updated_at = datetime.now(UTC)
        self.session.flush()
        return self._dict(row)

    def replace_subject_relation(self, resource_id, subject_id):
        self.session.execute(delete(ResourceSubject).where(ResourceSubject.resource_id == resource_id))
        if subject_id is not None:
            self.session.add(ResourceSubject(resource_id=resource_id, subject_id=subject_id, is_primary=True))
        self.session.flush()

    def replace_grade_relation(self, resource_id, grade_level_id):
        self.session.execute(delete(ResourceGradeLevel).where(ResourceGradeLevel.resource_id == resource_id))
        if grade_level_id is not None:
            self.session.add(ResourceGradeLevel(resource_id=resource_id, grade_level_id=grade_level_id))
        self.session.flush()

    def get_resource_subject_id(self, resource_id):
        return self.session.scalar(
            select(ResourceSubject.subject_id).where(ResourceSubject.resource_id == resource_id).limit(1)
        )

    def get_resource_grade_level_id(self, resource_id):
        return self.session.scalar(
            select(ResourceGradeLevel.grade_level_id).where(ResourceGradeLevel.resource_id == resource_id).limit(1)
        )
