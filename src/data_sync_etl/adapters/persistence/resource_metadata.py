from datetime import UTC, datetime

from sqlalchemy import delete, func, select

from data_sync_etl.db.models import (
    EtlStageRun,
    MasterLearningResource,
    OcrJob,
    ResourceGradeLevel,
    ResourceSubject,
    ResourceVersion,
    SourceFileImport,
)
from data_sync_etl.domain.resource_metadata import assert_resource_metadata_editable


class ResourceMetadataRepositoryMixin:
    def resource_version_summaries(self, resource_ids):
        if not resource_ids:
            return {}

        def latest(model, partition, condition, order):
            ranked = select(*model.__table__.columns,
                func.row_number().over(partition_by=partition, order_by=order).label('position')
                ).where(condition).subquery()
            return [dict(row) for row in self.session.execute(
                select(ranked).where(ranked.c.position == 1).order_by(ranked.c.created_at.desc(), ranked.c.id.desc())
            ).mappings()]

        versions = latest(ResourceVersion, ResourceVersion.resource_id, ResourceVersion.resource_id.in_(resource_ids),
                          (ResourceVersion.version_number.desc(), ResourceVersion.created_at.desc(), ResourceVersion.id.desc()))
        ids = [version['id'] for version in versions]
        if not ids:
            return {}
        jobs = {row['resource_version_id']: row for row in latest(OcrJob, OcrJob.resource_version_id,
            OcrJob.resource_version_id.in_(ids), (OcrJob.created_at.desc(), OcrJob.id.desc()))}
        stages = latest(EtlStageRun, (EtlStageRun.resource_version_id, EtlStageRun.stage),
                        EtlStageRun.resource_version_id.in_(ids), (EtlStageRun.created_at.desc(), EtlStageRun.id.desc()))
        imports = {row['target_version_id']: row for row in latest(SourceFileImport, SourceFileImport.target_version_id,
            SourceFileImport.target_version_id.in_(ids), (SourceFileImport.created_at.desc(), SourceFileImport.id.desc()))}
        return {version['resource_id']: {
            'id': version['id'], 'version_number': version['version_number'], 'created_at': version['created_at'],
            'lifecycle_status': version['lifecycle_status'], 'job': jobs.get(version['id']),
            'stages': [row for row in stages if row['resource_version_id'] == version['id']],
            'source_version_id': imports.get(version['id'], {}).get('source_version_id'),
        } for version in versions}

    def get_resource_for_update(self, resource_id):
        row = self.session.execute(
            select(MasterLearningResource)
            .where(MasterLearningResource.id == resource_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        return self._dict(row) if row else None

    def update_resource_metadata(self, resource_id, values):
        mapping = {
            "title": "title",
            "resource_type_code": "resource_type_code",
            "resource_type_id": "resource_type_id",  # Internal SOURCE resolution only; not an HTTP input.
            "resource_type_name_vi": "resource_type_name_vi",
            "provider_id": "provider_id",
            "provider_name": "provider_name",
            "provider_type": "provider_type",
            "publication_status": "publication_status",
            "uploaded_by": "created_by",
        }
        row = self._metadata_row_for_update(resource_id)
        visibility_changed = 'publication_status' in values and values['publication_status'] != row.publication_status
        for source, target in mapping.items():
            if source in values:
                setattr(row, target, values[source])
        row.updated_at = datetime.now(UTC)
        self.session.flush()
        if visibility_changed:
            self.bump_resource_visibility_epoch()
        return self._dict(row)

    def replace_subject_relation(self, resource_id, subject_id):
        self._metadata_row_for_update(resource_id)
        self.session.execute(delete(ResourceSubject).where(ResourceSubject.resource_id == resource_id))
        if subject_id is not None:
            self.session.add(ResourceSubject(resource_id=resource_id, subject_id=subject_id, is_primary=True))
        self.session.flush()

    def replace_grade_relation(self, resource_id, grade_level_id):
        self._metadata_row_for_update(resource_id)
        self.session.execute(delete(ResourceGradeLevel).where(ResourceGradeLevel.resource_id == resource_id))
        if grade_level_id is not None:
            self.session.add(ResourceGradeLevel(resource_id=resource_id, grade_level_id=grade_level_id))
        self.session.flush()

    def get_resource_subject_id(self, resource_id):
        return self.session.scalar(
            select(ResourceSubject.subject_id).where(ResourceSubject.resource_id == resource_id).limit(1)
        )

    def _metadata_row_for_update(self, resource_id):
        row = self.session.execute(select(MasterLearningResource).where(
            MasterLearningResource.id == resource_id,
        ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        if row is None:
            raise LookupError('Resource not found')
        assert_resource_metadata_editable(self._dict(row))
        return row

    def get_resource_grade_level_id(self, resource_id):
        return self.session.scalar(
            select(ResourceGradeLevel.grade_level_id).where(ResourceGradeLevel.resource_id == resource_id).limit(1)
        )
