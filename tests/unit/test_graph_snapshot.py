from copy import deepcopy

import pytest

from data_sync_etl.adapters.persistence.graph_snapshot import verify_bag_roots
from data_sync_etl.domain.graph_contract import LABELS, GraphContractError, build_snapshot, graph_id


def graph_tables():
    return {
        'master_grade_levels': [dict(id='master-grade', education_level_id='source-education',
                                    education_code='E', education_name_vi='Level')],
        'subjects': [dict(id='master-subject', grade_id='master-grade')],
        'master_learning_resources': [dict(id='master-resource')],
        'resource_versions': [dict(id='master-v1', resource_id='master-resource', parent_version_id=None),
                              dict(id='master-v2', resource_id='master-resource', parent_version_id='master-v1')],
        'content_units': [dict(id='master-root', resource_version_id='master-v2', parent_unit_id=None),
                          dict(id='derived-leaf', resource_version_id='master-v2', parent_unit_id='master-root')],
        'resource_subjects': [dict(resource_id='master-resource', subject_id='master-subject')],
        'resource_grade_levels': [dict(resource_id='master-resource', grade_level_id='master-grade')],
    }


def test_exact_six_labels_eight_shapes_and_master_ids():
    s = build_snapshot(graph_tables())
    assert {n[0] for n in s.nodes} == set(LABELS)
    assert len(s.nodes) == 8 and len(s.edges) == 10
    assert ('ContentUnit', 'derived-leaf') in s.nodes
    assert ('ResourceVersion', 'master-v2', 'CONTAINS', 'ContentUnit', 'derived-leaf') in s.edges
    assert verify_bag_roots(s, [{'_id': 'master-root', 'resource_version_id': 'master-v2'}])['matched'] == 1
    assert verify_bag_roots(s, [{'_id': 'master-root', 'resource_version_id': 'master-v1'}])['matched'] == 0
    assert verify_bag_roots(s, [{'_id': 'source-root', 'resource_version_id': 'master-v2'}])['matched'] == 0


def test_empty_snapshot_and_determinism():
    tables = graph_tables()
    s = build_snapshot(tables)
    assert s == build_snapshot({k: list(reversed(v)) for k, v in tables.items()})
    assert s.fingerprint == build_snapshot(deepcopy(tables)).fingerprint
    assert not build_snapshot({k: [] for k in tables}).nodes


@pytest.mark.parametrize('value', ['', ' x', 'x ', None, 42, 'a\nb', 'https://a', 'e\u0301', 'x'*31])
def test_invalid_id_never_silently_rewritten(value):
    with pytest.raises(GraphContractError, match='GRAPH_ID_INVALID'):
        graph_id(value)


@pytest.mark.parametrize(('table', 'field', 'value'), [
    ('subjects', 'grade_id', 'missing'),
    ('resource_versions', 'resource_id', 'missing'),
    ('content_units', 'resource_version_id', 'missing'),
    ('content_units', 'parent_unit_id', 'missing'),
    ('content_units', 'parent_unit_id', 'master-root'),
    ('resource_versions', 'parent_version_id', 'missing'),
])
def test_orphans_and_self_cycles_rejected(table, field, value):
    t = graph_tables()
    t[table][0][field] = value
    with pytest.raises(GraphContractError):
        build_snapshot(t)


def test_cross_version_parent_and_long_cycle():
    t = graph_tables()
    t['content_units'][1]['resource_version_id'] = 'master-v1'
    with pytest.raises(GraphContractError, match='GRAPH_UNIT_PARENT_INVALID'):
        build_snapshot(t)
    t = graph_tables()
    t['content_units'][0]['parent_unit_id'] = 'derived-leaf'
    with pytest.raises(GraphContractError, match='GRAPH_PARENT_CYCLE'):
        build_snapshot(t)


@pytest.mark.parametrize('table', ['subjects', 'resource_subjects', 'resource_grade_levels'])
def test_duplicate_rows_rejected(table):
    t = graph_tables()
    t[table].append(deepcopy(t[table][0]))
    with pytest.raises(GraphContractError, match='GRAPH_DUPLICATE'):
        build_snapshot(t)


def test_flat_education_conflict_and_nullable_grade():
    t = graph_tables()
    t['subjects'][0]['grade_id'] = None
    assert build_snapshot(t).counts()['edges']['GradeLevel/HAS_SUBJECT/Subject'] == 0
    t['master_grade_levels'].append({**t['master_grade_levels'][0], 'id': 'grade2', 'education_code': 'other'})
    with pytest.raises(GraphContractError, match='GRAPH_EDUCATION_IDENTITY_CONFLICT'):
        build_snapshot(t)


def test_full_history_including_deleted_and_unprocessed_versions():
    t = graph_tables()
    t['master_learning_resources'][0].update(is_active=False, deleted_at='test-only', publication_status='DRAFT')
    s = build_snapshot(t)
    assert ('LearningResource', 'master-resource') in s.nodes
    assert ('ResourceVersion', 'master-v1') in s.nodes
    assert not any(e[1] == 'master-v1' and e[2] == 'CONTAINS' for e in s.edges)


def test_federated_master_and_source_ids_are_not_interchangeable():
    from data_sync_etl.adapters.persistence.graph_snapshot import MasterGraphReader
    from data_sync_etl.domain.source_ids import SourceLocator, source_identity_hash
    t = graph_tables()
    resource = t['master_learning_resources'][0]
    locator = SourceLocator(backend='postgresql', namespace='postgresql:learning',
                            entity_type='resources', source_record_id='source-resource')
    resource.update(source_identity_version=1, source_backend=locator.backend,
                    source_namespace=locator.namespace, source_entity_type='resources',
                    source_record_key=locator.source_record_id, source_identity_hash=source_identity_hash(locator))
    for v in t['resource_versions']:
        loc = SourceLocator(backend=locator.backend, namespace=locator.namespace,
                            entity_type='versions', source_record_id='source-'+v['id'])
        v['processing_options'] = {'source_version_identity': {
            'backend':loc.backend, 'namespace':loc.namespace, 'source_version_id':loc.source_record_id,
            'source_resource_id':'source-resource', 'identity_hash':source_identity_hash(loc)}}
    MasterGraphReader.verify_identities(t)
    snapshot = build_snapshot(t)
    assert ('LearningResource', 'master-resource') in snapshot.nodes
    assert ('LearningResource', 'source-resource') not in snapshot.nodes
    t['resource_versions'][0]['processing_options']['source_version_identity']['identity_hash'] = '0'*64
    with pytest.raises(GraphContractError, match='GRAPH_VERSION_MAPPING_INVALID'):
        MasterGraphReader.verify_identities(t)


def test_reader_keyset_is_complete_and_select_only():
    from sqlalchemy import Column, MetaData, String, Table, create_engine, event

    from data_sync_etl.adapters.persistence.graph_snapshot import FIELDS, MasterGraphReader

    engine = create_engine('sqlite://')
    tables, data = {}, graph_tables()
    meta = MetaData()
    for name, fields in FIELDS.items():
        keys = ['id'] if 'id' in fields else list(fields)
        tables[name] = Table(name, meta, *(Column(f, String, primary_key=f in keys) for f in fields))
    meta.create_all(engine)
    with engine.begin() as c:
        for name, rows in data.items():
            for row in rows:
                c.execute(tables[name].insert().values(**row))
    statements = []
    event.listen(engine, 'before_cursor_execute', lambda c, cur, stmt, params, ctx, many: statements.append(stmt))
    snapshot = MasterGraphReader(engine, batch_size=1).read()
    assert snapshot == build_snapshot(data)
    assert statements and all(s.startswith('SELECT') for s in statements)
    assert sum('FROM content_units' in s for s in statements) == 3
    with pytest.raises(GraphContractError, match='GRAPH_SNAPSHOT_CAPACITY_EXCEEDED'):
        MasterGraphReader(engine, max_records=1).read()
    engine.dispose()


def test_all_derived_units_included_without_source_counterpart():
    t = graph_tables()
    t['content_units'] = [dict(id=f'master-derived-{i}', resource_version_id='master-v2',
                               parent_unit_id=None if i == 0 else 'master-derived-0') for i in range(41)]
    assert build_snapshot(t).counts()['nodes']['ContentUnit'] == 41
    assert build_snapshot(t).counts()['edges']['ContentUnit/HAS_CHILD/ContentUnit'] == 40


def test_mongo_content_unit_mapping_uses_master_id_and_version():
    from data_sync_etl.adapters.persistence.graph_snapshot import verify_content_mapping
    s = build_snapshot(graph_tables())
    report = verify_content_mapping(s, [
        {'_id':'derived-leaf', 'content_unit_id':'derived-leaf', 'resource_version_id':'master-v2'},
        {'_id':'master-root', 'resource_version_id':'master-v2'},
        {'_id':'source-root', 'resource_version_id':'master-v2'},
        {'_id':'master-root', 'content_unit_id':'other', 'resource_version_id':'master-v2'},
        {'_id':'master-root', 'resource_version_id':'master-v1'},
    ])
    assert report == {'current_content_units':5, 'matched':2, 'mapping_drift':3}
