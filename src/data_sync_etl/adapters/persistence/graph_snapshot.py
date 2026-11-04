"""Graph input boundary: SELECT-only, one stable MASTER snapshot, keyset pages."""

from sqlalchemy import select, text

from data_sync_etl.db.models import (
    ContentUnit,
    MasterGradeLevel,
    MasterLearningResource,
    ResourceGradeLevel,
    ResourceSubject,
    ResourceVersion,
    Subject,
)
from data_sync_etl.domain.graph_contract import GraphContractError, build_snapshot
from data_sync_etl.domain.source_ids import (
    SourceLocator,
    mapped_source_locator,
    source_identity_hash,
)

MODELS = (MasterGradeLevel, Subject, MasterLearningResource, ResourceVersion,
          ContentUnit, ResourceSubject, ResourceGradeLevel)
# Exclude raw text, files, users and credentials even from the in-memory reader.
IDENTITY = ('source_namespace', 'source_backend', 'source_entity_type', 'source_record_key',
            'source_identity_hash', 'source_identity_version')
FIELDS = {
    'master_grade_levels': ('id', 'education_level_id', 'education_code', 'education_name_vi', *IDENTITY),
    'subjects': ('id', 'grade_id', *IDENTITY),
    'master_learning_resources': ('id', *IDENTITY),
    'resource_versions': ('id', 'resource_id', 'parent_version_id', 'processing_options'),
    'content_units': ('id', 'resource_version_id', 'parent_unit_id', 'mongo_id'),
    'resource_subjects': ('resource_id', 'subject_id'),
    'resource_grade_levels': ('resource_id', 'grade_level_id'),
}


class MasterGraphReader:
    def __init__(self, engine, *, batch_size=200, max_records=1_000_000):
        self.engine, self.batch_size, self.max_records = engine, batch_size, max_records
        if not 1 <= batch_size <= 1000 or max_records < 1:
            raise GraphContractError('GRAPH_READER_LIMIT_INVALID')

    def read(self):
        tables, count = {}, 0
        with self.engine.connect() as c, c.begin():
            if c.dialect.name == 'postgresql':
                c.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
                c.execute(text("SET LOCAL statement_timeout = '15000ms'"))
            elif c.dialect.name != 'sqlite':
                raise GraphContractError('GRAPH_DATABASE_UNSUPPORTED')
            for model in MODELS:
                table = model.__table__
                cols = [table.c[name] for name in FIELDS[table.name]]
                keys = list(table.primary_key.columns)
                rows, after = [], None
                while True:
                    stmt = select(*cols).order_by(*keys).limit(self.batch_size)
                    if after is not None:
                        from sqlalchemy import tuple_
                        stmt = stmt.where(tuple_(*keys) > tuple_(*after))
                    page = [dict(row) for row in c.execute(stmt).mappings()]
                    count += len(page)
                    if count > self.max_records:
                        raise GraphContractError('GRAPH_SNAPSHOT_CAPACITY_EXCEEDED')
                    rows.extend(page)
                    if len(page) < self.batch_size:
                        break
                    after = tuple(page[-1][col.name] for col in keys)
                tables[table.name] = rows
            self.verify_identities(tables)
            return build_snapshot(tables)

    @staticmethod
    def verify_identities(tables):
        resources = {row['id']: row for row in tables['master_learning_resources']}
        for name in ('master_grade_levels', 'subjects', 'master_learning_resources'):
            for row in tables[name]:
                values = [row.get(key) for key in IDENTITY]
                if any(value is not None for value in values):
                    try:
                        mapped_source_locator(row)
                    except Exception:
                        raise GraphContractError('GRAPH_FEDERATION_IDENTITY_INVALID') from None
        for version in tables['resource_versions']:
            resource = resources.get(version['resource_id'])
            identity = (version.get('processing_options') or {}).get('source_version_identity')
            if resource and resource.get('source_namespace') and not identity:
                raise GraphContractError('GRAPH_VERSION_MAPPING_MISSING')
            if identity:
                try:
                    locator = SourceLocator(backend=identity['backend'], namespace=identity['namespace'],
                                            entity_type='versions', source_record_id=identity['source_version_id'])
                    if (source_identity_hash(locator) != identity['identity_hash'] or not resource
                            or locator.namespace != resource['source_namespace']
                            or locator.backend != resource['source_backend']
                            or identity['source_resource_id'] != resource['source_record_key']):
                        raise ValueError()
                except Exception:
                    raise GraphContractError('GRAPH_VERSION_MAPPING_INVALID') from None


def verify_bag_roots(snapshot, bags):
    """Read IDs/version only from current bags; never create fake graph nodes."""
    memberships = {(e[4], e[1]) for e in snapshot.edges if e[2] == 'CONTAINS'}
    total = matched = 0
    for bag in bags:
        total += 1
        if (bag['_id'], bag['resource_version_id']) in memberships:
            matched += 1
    return {'current_bags': total, 'matched': matched, 'missing_or_wrong_version': total - matched}
