from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from data_sync_etl.adapters.source.postgresql_ocr_read import PostgreSQLSourceOCRReader
from data_sync_etl.domain.core import DomainError


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def one_or_none(self):
        return self.rows[0] if self.rows else None


class SourceDouble:
    dialect = SimpleNamespace(name='postgresql')

    def __init__(self):
        self.version = dict(id='v1', source_hash='a' * 64, lifecycle_status='APPROVED', updated_at=None)
        self.jobs = [dict(id='j1', resource_version_id='v1', status='PROCESSING', total_pages=2,
                          completed_pages=1, failed_pages=0, started_at=None, completed_at=None, updated_at=None)]
        self.pages = [dict(id=f'p{number}', job_id='j1', page_num=number,
                           status='COMPLETED' if number == 1 else 'PENDING', retry_count=0,
                           updated_at=None, completed_at=None) for number in (1, 2)]
        self.units = [dict(id=f'u{number}', mongo_id=None, resource_version_id='v1',
                           parent_unit_id=None if number == 1 else 'u1', unit_type='CHAPTER',
                           sequence_no=number, page_from=1, page_to=2, review_status='APPROVED',
                           retrieval_eligible=True, updated_at=None) for number in (1, 2)]

    @contextmanager
    def begin(self):
        yield self

    def execute(self, statement, params=None):
        sql = str(statement)
        assert sql.startswith(('SELECT ', 'SET TRANSACTION ')), 'Reader must be read-only'
        for table, rows in [('resource_versions', [self.version]), ('ocr_jobs', self.jobs),
                            ('ocr_page_tasks', self.pages), ('content_units', self.units)]:
            if f'FROM public.{table} ' in sql:
                return Result([dict(row) for row in rows])
        return Result([])


@pytest.fixture
def source():
    return SourceDouble()


def read(engine, authorize=lambda _identity: True, **changes):
    identity = dict(namespace='ns', source_version_id='v1', master_version_id='m1', source_hash='a' * 64)
    return PostgreSQLSourceOCRReader(engine, authorize=authorize,
                                     lifecycle_allowed=lambda value: value == 'APPROVED').snapshot(**(identity | changes))


def test_read_source_state_without_content_or_private_paths(source):
    source.units[0]['review_status'] = 'PENDING'
    authorized = []
    result = read(source, lambda identity: authorized.append(identity) or True)
    assert len(authorized) == 2
    assert result['job']['status'] == 'RUNNING'
    assert [page['status'] for page in result['pages']] == ['SUCCEEDED', 'PENDING']
    assert result['units'][1]['parent_unit_id'] == 'u1'
    assert result['units'][0]['review_status'] == 'UNREVIEWED'
    assert all('local_image_path' not in page and 'raw_text' not in page for page in result['pages'])


def test_rejects_stale_versions_and_duplicate_pages(source):
    with pytest.raises(DomainError, match='SOURCE_VERSION_MUTATED'):
        read(source, source_hash='b' * 64)
    source.pages.append(dict(source.pages[0], id='p3'))
    with pytest.raises(DomainError, match='SOURCE_OCR_PAGE_CONFLICT'):
        read(source)


def test_rejects_cyclic_units(source):
    source.units[0]['parent_unit_id'] = 'u2'
    with pytest.raises(DomainError, match='SOURCE_OCR_UNIT_CONFLICT'):
        read(source)


def test_denied_or_revoked_source_mapping_fails_closed(source):
    with pytest.raises(DomainError, match='SOURCE_OCR_UNAUTHORIZED'):
        read(source, lambda _: False)
    answers = iter([True, False])
    with pytest.raises(DomainError, match='SOURCE_OCR_UNAUTHORIZED'):
        read(source, lambda _: next(answers))
    source.version['lifecycle_status'] = 'REJECTED'
    with pytest.raises(DomainError, match='SOURCE_OCR_LIFECYCLE_BLOCKED'):
        read(source)
