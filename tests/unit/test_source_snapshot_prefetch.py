from copy import deepcopy

import pytest

from data_sync_etl.adapters.source.aggregate import build_aggregate
from data_sync_etl.adapters.source.postgresql import SnapshotReader
from data_sync_etl.domain.core import DomainError, now


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)

    def one_or_none(self):
        return self.rows[0] if self.rows else None


class Connection:
    def __init__(self):
        self.calls = []
        self.stamp = now()
        self.providers = [{'id': 'p1', 'name': 'Provider', 'provider_type': 'OPEN',
                           'created_at': self.stamp, 'updated_at': self.stamp}]
        self.types = [{'id': 't1', 'code': 'TEXT', 'name_vi': 'Text',
                      'created_at': self.stamp, 'updated_at': self.stamp}]

    def execute(self, statement, params):
        sql = str(statement)
        self.calls.append(sql)
        ids = params.get('ids', [params.get('id')])
        if 'FROM providers' in sql:
            return Result(deepcopy([row for row in self.providers if row['id'] in ids]))
        if 'FROM resource_types' in sql:
            return Result(deepcopy([row for row in self.types if row['id'] in ids]))
        if 'FROM resource_subjects' in sql:
            return Result([{'resource_id': key, 'subject_id': 's1', 'is_primary': True,
                            'updated_at': self.stamp} for key in ids])
        if 'FROM resource_grade_levels' in sql:
            return Result([{'resource_id': key, 'grade_level_id': 'g1',
                            'updated_at': self.stamp} for key in ids])
        raise AssertionError('Unexpected SQL')


def resources(connection, count=10):
    return [{'id': f'r{index}', 'provider_id': 'p1', 'resource_type_id': 't1',
             'title': 'Learning resource', 'publication_status': 'PUBLISHED',
             'created_at': connection.stamp, 'updated_at': connection.stamp} for index in range(count)]


def test_page_prefetch_preserves_complete_aggregates_and_cursor_with_four_queries():
    point, batch = Connection(), Connection()
    batch.stamp, batch.providers, batch.types = point.stamp, deepcopy(point.providers), deepcopy(point.types)
    rows = resources(point)
    old_reader = SnapshotReader(point)
    expected = [build_aggregate(old_reader, 'resources', old_reader.record('resources', row)).model_dump()
                for row in rows]
    reader = SnapshotReader(batch)
    reader.prepare_page('resources', rows)
    observed = [build_aggregate(reader, 'resources', reader.record('resources', row)).model_dump()
                for row in rows]
    assert observed == expected
    assert len(point.calls) == 40 and len(batch.calls) == 4


def test_prefetch_does_not_survive_next_page_or_share_mutable_models():
    connection = Connection()
    reader = SnapshotReader(connection)
    reader.prepare_page('resources', resources(connection, 1))
    first = reader.get_provider('p1')
    first.name = 'Caller mutation'
    assert reader.get_provider('p1').name == 'Provider'
    connection.providers[0]['name'] = 'Fresh next page'
    reader.prepare_page('resources', resources(connection, 1))
    assert reader.get_provider('p1').name == 'Fresh next page'
    assert len(connection.calls) == 8


def test_missing_dependency_fails_closed_before_aggregate_is_consumed():
    connection = Connection()
    connection.providers = []
    with pytest.raises(DomainError, match='dependency missing'):
        SnapshotReader(connection).prepare_page('resources', resources(connection))


def test_oversized_page_is_rejected_without_database_work():
    connection = Connection()
    with pytest.raises(DomainError, match='PAGE_LIMIT'):
        SnapshotReader(connection).prepare_page('resources', resources(connection, 1001))
    assert connection.calls == []
