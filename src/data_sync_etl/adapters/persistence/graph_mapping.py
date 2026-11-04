"""Batched authoritative identity/relationship verification for graph candidates."""

from sqlalchemy import select, text

from data_sync_etl.db.models import (
    ContentUnit,
    ResourceGradeLevel,
    ResourceSubject,
    ResourceVersion,
)


class PostgresGraphMapping:
    def __init__(self, engine, timeout_seconds=3):
        self.engine, self.timeout_seconds = engine, timeout_seconds

    def resource_relations(self, ids):
        from data_sync_etl.db.models import MasterGradeLevel, Subject

        if not 1 <= len(ids) <= 100 or len(set(ids)) != len(ids):
            raise ValueError('GRAPH_CANDIDATE_LIMIT')
        result = {key: {'subject_ids': [], 'grade_ids': []} for key in ids}
        with self.engine.connect() as c, c.begin():
            if c.dialect.name == 'postgresql':
                c.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
                c.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                          {'timeout': str(int(self.timeout_seconds * 1000)) + 'ms'})
            for key, subject in c.execute(select(ResourceSubject.resource_id, ResourceSubject.subject_id)
                    .join(Subject, Subject.id == ResourceSubject.subject_id)
                    .where(ResourceSubject.resource_id.in_(ids), Subject.is_active.is_(True),
                           Subject.deleted_at.is_(None))):
                result[key]['subject_ids'].append(subject)
            for key, grade in c.execute(select(ResourceGradeLevel.resource_id, ResourceGradeLevel.grade_level_id)
                    .join(MasterGradeLevel, MasterGradeLevel.id == ResourceGradeLevel.grade_level_id)
                    .where(ResourceGradeLevel.resource_id.in_(ids), MasterGradeLevel.grade_is_active.is_(True),
                           MasterGradeLevel.grade_deleted_at.is_(None),
                           MasterGradeLevel.education_is_active.is_(True),
                           MasterGradeLevel.education_deleted_at.is_(None))):
                result[key]['grade_ids'].append(grade)
        return {key: {field: sorted(set(values)) for field, values in fields.items()}
                for key, fields in result.items()}

    def resolve(self, ids):
        with self.engine.connect() as c, c.begin():
            if c.dialect.name == 'postgresql':
                c.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
                c.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                          {'timeout':str(int(self.timeout_seconds*1000))+'ms'})
            rows = [dict(row) for row in c.execute(select(
                ContentUnit.id.label('content_unit_id'), ContentUnit.resource_version_id,
                ResourceVersion.resource_id).join(ResourceVersion,
                    ContentUnit.resource_version_id == ResourceVersion.id).where(ContentUnit.id.in_(ids))).mappings()]
            resources = {row['resource_id'] for row in rows}
            subjects, grades = {key: [] for key in resources}, {key: [] for key in resources}
            for resource, subject in c.execute(select(ResourceSubject.resource_id, ResourceSubject.subject_id).where(
                    ResourceSubject.resource_id.in_(resources))):
                subjects[resource].append(subject)
            for resource, grade in c.execute(select(ResourceGradeLevel.resource_id, ResourceGradeLevel.grade_level_id).where(
                    ResourceGradeLevel.resource_id.in_(resources))):
                grades[resource].append(grade)
            return [{**row, 'subject_ids': sorted(subjects[row['resource_id']]),
                     'grade_ids': sorted(grades[row['resource_id']])} for row in rows]
