"""Parameterized metadata selection. Latest eligible lifecycle version, never arbitrary history."""

from sqlalchemy import and_, exists, func, or_, select

from data_sync_etl.db.models import (
    ContentUnit,
    ContentUnitText,
    EtlStageRun,
    MasterGradeLevel,
    MasterLearningResource,
    OcrJob,
    ResourceGradeLevel,
    ResourceSubject,
    ResourceVersion,
    SourceFileImport,
    Subject,
)
from data_sync_etl.domain.evaluation import EvaluationError
from data_sync_etl.domain.indexing import IndexingError, digest

SEARCH_TEXT_BATCH_SIZE = 500


class SearchRepositoryMixin:
    def metadata_refresh_page(self, after, limit):
        query = select(MasterLearningResource.id).order_by(MasterLearningResource.id).limit(limit)
        if after:
            query = query.where(MasterLearningResource.id > after)
        return list(self.session.scalars(query))

    def metadata_snapshots(self, filters, access, *, limit):
        """Batch authority reads; metadata extends only the selected current version."""
        filters = dict(filters)
        requested_version = filters.pop('resource_version_id', None)
        rows = self.search_versions(filters, limit=limit, metadata_access=access)
        if requested_version:
            rows = [row for row in rows if row['version_id'] == requested_version]
        resource_ids = [row['resource_id'] for row in rows]
        version_ids = [row['version_id'] for row in rows]
        if not rows:
            return []
        resources = {obj.id: self._dict(obj) for obj in self.session.scalars(
            select(MasterLearningResource).where(MasterLearningResource.id.in_(resource_ids)))}
        versions = {obj.id: self._dict(obj) for obj in self.session.scalars(
            select(ResourceVersion).where(ResourceVersion.id.in_(version_ids)))}
        imports = {}
        for obj in self.session.scalars(select(SourceFileImport).where(SourceFileImport.target_version_id.in_(version_ids))):
            imports.setdefault(obj.target_version_id, []).append(self._dict(obj))
        curriculum = {key: {'subjects': [], 'grades': []} for key in resource_ids}
        for key, name in self.session.execute(select(ResourceSubject.resource_id, Subject.subject_name).join(
            Subject, Subject.id == ResourceSubject.subject_id).where(ResourceSubject.resource_id.in_(resource_ids),
            Subject.is_active.is_(True), Subject.deleted_at.is_(None))):
            curriculum[key]['subjects'].append(name)
        for key, name in self.session.execute(select(ResourceGradeLevel.resource_id, MasterGradeLevel.grade_name_vi).join(
            MasterGradeLevel, MasterGradeLevel.id == ResourceGradeLevel.grade_level_id).where(
            ResourceGradeLevel.resource_id.in_(resource_ids), MasterGradeLevel.grade_is_active.is_(True),
            MasterGradeLevel.grade_deleted_at.is_(None), MasterGradeLevel.education_is_active.is_(True),
            MasterGradeLevel.education_deleted_at.is_(None))):
            curriculum[key]['grades'].append(name)
        for fields in curriculum.values():
            for key in fields:
                fields[key] = sorted(set(fields[key]))
                if len(fields[key]) > 32:
                    raise IndexingError('SEMANTIC_METADATA_LIMIT', 422)
        return [(row, resources.get(row['resource_id']), versions.get(row['version_id']),
                 curriculum[row['resource_id']], imports.get(row['version_id'], [])) for row in rows]

    def semantic_metadata(self, resource_id):
        """Verified human-readable curriculum labels; no identifiers in model input."""
        title = self.session.scalar(select(MasterLearningResource.title).where(MasterLearningResource.id == resource_id))
        subjects = list(self.session.scalars(select(Subject.subject_name).join(
            ResourceSubject, ResourceSubject.subject_id == Subject.id).where(
                ResourceSubject.resource_id == resource_id, Subject.is_active.is_(True),
                Subject.deleted_at.is_(None)).order_by(Subject.subject_name).limit(33)))
        grades = list(self.session.scalars(select(MasterGradeLevel.grade_name_vi).join(
            ResourceGradeLevel, ResourceGradeLevel.grade_level_id == MasterGradeLevel.id).where(
                ResourceGradeLevel.resource_id == resource_id,
                MasterGradeLevel.grade_is_active.is_(True), MasterGradeLevel.grade_deleted_at.is_(None),
                MasterGradeLevel.education_is_active.is_(True), MasterGradeLevel.education_deleted_at.is_(None)
                ).order_by(MasterGradeLevel.grade_name_vi).limit(33)))
        if len(subjects) > 32 or len(grades) > 32:
            raise IndexingError('SEMANTIC_METADATA_LIMIT', 422)
        return {'title': title, 'subjects': sorted(set(subjects)), 'grades': sorted(set(grades))}

    def search_unit_texts(self, unit_ids):
        """Bounded request/phase-local hydration, using this repository's session.

        No ordering/paging/authorization decisions: the snapshot keeps its exact
        unit order and validates missing text against the live Mongo projection.
        Never retain this mapping across snapshot phases or requests.
        """
        ids = tuple(unit_ids)
        if (len(ids) > 10000 or len(set(ids)) != len(ids)
                or any(type(key) is not str or not 1 <= len(key) <= 30 for key in ids)):
            raise IndexingError('CONTENT_NOT_READY')
        result = {}
        for offset in range(0, len(ids), SEARCH_TEXT_BATCH_SIZE):
            batch = ids[offset:offset + SEARCH_TEXT_BATCH_SIZE]
            rows = self.session.execute(select(ContentUnitText.content_unit_id, ContentUnitText.text)
                                        .where(ContentUnitText.content_unit_id.in_(batch))).mappings()
            for row in rows:
                key = row['content_unit_id']
                if key in result:
                    raise IndexingError('CONTENT_NOT_READY')
                result[key] = dict(row)
        return result

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

    def search_versions(self, filters, *, limit, metadata_access=None):
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
                MasterGradeLevel.grade_is_active.is_(True), MasterGradeLevel.grade_deleted_at.is_(None),
                MasterGradeLevel.education_is_active.is_(True), MasterGradeLevel.education_deleted_at.is_(None)))
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
        if filters.get('resource_format'):
            format_version = v.__table__.alias('format_version')
            suffix = filters['resource_format']
            suffixes = ('.jpg', '.jpeg') if suffix in ('jpg', 'jpeg') else ('.' + suffix,)
            stmt = stmt.where(exists().where(format_version.c.id == ranked.c.version_id,
                or_(*(func.lower(format_version.c.storage_object_key).endswith(value, autoescape=True)
                      for value in suffixes))))
        if metadata_access is not None:
            if not metadata_access.internal:
                return []
            if metadata_access.scope == 'authenticated_user':
                access = metadata_access
                owned = and_(r.id.in_(access.resource_ids), r.created_by == access.owner_id) if access.owner_id else False
                stmt = stmt.join(v, v.id == ranked.c.version_id)
                if not access.allow_public:
                    stmt = stmt.where(owned)
                stmt = stmt.where(or_(and_(v.final_approved_at.is_not(None), v.final_approved_by.is_not(None)),
                                      owned if access.allow_owned_unreviewed else False))
        if filters.get('resource_version_id'):
            stmt = stmt.where(ranked.c.version_id == filters['resource_version_id'])
        else:
            stmt = stmt.where(ranked.c.position == 1)
        return [dict(row) for row in self.session.execute(
            stmt.order_by(r.id, ranked.c.version_id).limit(limit)).mappings()]
