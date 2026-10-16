"""Parameterized metadata selection. Latest eligible lifecycle version, never arbitrary history."""

from sqlalchemy import exists, func, select

from data_sync_etl.db.models import (
    ContentUnit,
    EtlStageRun,
    MasterGradeLevel,
    MasterLearningResource,
    OcrJob,
    ResourceGradeLevel,
    ResourceSubject,
    ResourceVersion,
    Subject,
)
from data_sync_etl.domain.evaluation import EvaluationError
from data_sync_etl.domain.indexing import digest


class SearchRepositoryMixin:
    def evaluation_state(self, filters, *, limit, max_units):
        """Bounded PG-only change marker; no text, embeddings or per-unit queries."""
        metadata = self.search_versions(filters, limit=limit + 1)
        if len(metadata) > limit:
            raise EvaluationError('EVALUATION_CORPUS_LIMIT')
        versions = [row['version_id'] for row in metadata]
        resources = [row['resource_id'] for row in metadata]

        def rows(stmt, cap):
            result = [dict(row) for row in self.session.execute(stmt.limit(cap + 1)).mappings()]
            if len(result) > cap:
                raise EvaluationError('EVALUATION_CORPUS_LIMIT')
            return result

        state = {'metadata': metadata}
        for name, model, condition, cap in [
            ('resources', MasterLearningResource, MasterLearningResource.id.in_(resources), limit),
            ('versions', ResourceVersion, ResourceVersion.id.in_(versions), limit),
            ('units', ContentUnit, ContentUnit.resource_version_id.in_(versions), max_units),
        ]:
            state[name] = rows(select(*model.__table__.columns).where(condition).order_by(model.id), cap)
        # Latest stage/job plus counts detects reprocessing, reindexing and projection generation.
        for name, model, partition in [
            ('stages', EtlStageRun, (EtlStageRun.resource_version_id, EtlStageRun.stage)),
            ('jobs', OcrJob, (OcrJob.resource_version_id,)),
        ]:
            columns = [column for column in model.__table__.columns if column.name != 'error_message']
            ranked = select(*columns,
                func.row_number().over(partition_by=partition, order_by=(model.created_at.desc(), model.id.desc())).label('position'),
                func.count().over(partition_by=partition).label('history_count')
                ).where(model.resource_version_id.in_(versions)).subquery()
            state[name] = rows(select(ranked).where(ranked.c.position == 1).order_by(ranked.c.id), limit * 20)
        return digest(state)

    def search_versions(self, filters, *, limit):
        r, v = MasterLearningResource, ResourceVersion
        conditions = [r.is_active.is_(True), r.deleted_at.is_(None), r.publication_status == 'PUBLISHED']
        if filters.get('resource_id'):
            conditions.append(r.id == filters['resource_id'])
        if filters.get('resource_type_code'):
            conditions.append(r.resource_type_code == filters['resource_type_code'])
        if filters.get('subject_id'):
            conditions.append(exists().where(
                ResourceSubject.resource_id == r.id, ResourceSubject.subject_id == filters['subject_id'],
                Subject.id == ResourceSubject.subject_id, Subject.is_active.is_(True), Subject.deleted_at.is_(None)))
        if filters.get('grade_level_id'):
            conditions.append(exists().where(
                ResourceGradeLevel.resource_id == r.id,
                ResourceGradeLevel.grade_level_id == filters['grade_level_id'],
                MasterGradeLevel.id == ResourceGradeLevel.grade_level_id,
                MasterGradeLevel.grade_is_active.is_(True), MasterGradeLevel.grade_deleted_at.is_(None)))
        # INGESTED is the local upload lifecycle. READY processing/index checks are
        # independent and mandatory in the application. Draft/rejected versions never qualify.
        eligible = ('INGESTED', 'APPROVED', 'PUBLISHED')
        ranked = select(v.id.label('version_id'), v.resource_id, v.version_number,
                        func.row_number().over(partition_by=v.resource_id,
                            order_by=(v.version_number.desc(), v.id.desc())).label('position')
                        ).where(v.lifecycle_status.in_(eligible)).subquery()
        stmt = select(r.id.label('resource_id'), r.title.label('resource_title'),
                      r.provider_name, r.resource_type_code, ranked.c.version_id,
                      ranked.c.version_number).join(ranked, ranked.c.resource_id == r.id).where(*conditions)
        if filters.get('resource_version_id'):
            stmt = stmt.where(ranked.c.version_id == filters['resource_version_id'])
        else:
            stmt = stmt.where(ranked.c.position == 1)
        return [dict(row) for row in self.session.execute(
            stmt.order_by(r.id, ranked.c.version_id).limit(limit)).mappings()]
