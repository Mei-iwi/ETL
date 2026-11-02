import pytest
from sqlalchemy import create_engine, text

from data_sync_etl.adapters.source.postgresql_ocr_read import PostgreSQLSourceOCRReader
from data_sync_etl.domain.core import DomainError


@pytest.fixture
def source():
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE resource_versions (id TEXT PRIMARY KEY, source_hash TEXT, '
                          'lifecycle_status TEXT, updated_at TEXT)'))
        conn.execute(text('CREATE TABLE ocr_jobs (id TEXT PRIMARY KEY, resource_version_id TEXT, status TEXT, '
                          'total_pages INTEGER, completed_pages INTEGER, failed_pages INTEGER, started_at TEXT, '
                          'completed_at TEXT, updated_at TEXT)'))
        conn.execute(text('CREATE TABLE ocr_page_tasks (id TEXT PRIMARY KEY, job_id TEXT, page_num INTEGER, '
                          'status TEXT, retry_count INTEGER, updated_at TEXT, completed_at TEXT, local_image_path TEXT)'))
        conn.execute(text('CREATE TABLE content_units (id TEXT PRIMARY KEY, mongo_id TEXT, resource_version_id TEXT, '
                          'parent_unit_id TEXT, unit_type TEXT, sequence_no INTEGER, page_from INTEGER, page_to INTEGER, '
                          'review_status TEXT, retrieval_eligible BOOLEAN, updated_at TEXT)'))
        conn.execute(text("INSERT INTO resource_versions VALUES ('v1', :hash, 'APPROVED', '2026-01-01')"),
                     {'hash': 'a' * 64})
        conn.execute(text("INSERT INTO ocr_jobs VALUES ('j1', 'v1', 'PROCESSING', 2, 1, 0, NULL, NULL, '2026-01-01')"))
        conn.execute(text("INSERT INTO ocr_page_tasks VALUES ('p1', 'j1', 1, 'COMPLETED', 0, '2026-01-01', NULL, 'private')"))
        conn.execute(text("INSERT INTO ocr_page_tasks VALUES ('p2', 'j1', 2, 'PENDING', 0, '2026-01-01', NULL, 'private')"))
        conn.execute(text("INSERT INTO content_units VALUES ('u1', NULL, 'v1', NULL, 'CHAPTER', 1, 1, 2, 'APPROVED', 1, '2026-01-01')"))
        conn.execute(text("INSERT INTO content_units VALUES ('u2', 'm2', 'v1', 'u1', 'SECTION', 2, 1, 1, 'APPROVED', 1, '2026-01-01')"))
    yield engine
    engine.dispose()


def read(engine, authorize=lambda _identity: True, **changes):
    identity = dict(namespace='ns', source_version_id='v1', master_version_id='m1', source_hash='a' * 64)
    return PostgreSQLSourceOCRReader(engine, authorize=authorize,
                                     lifecycle_allowed=lambda value: value == 'APPROVED').snapshot(**(identity | changes))


def test_read_source_state_without_content_or_private_paths(source):
    authorized = []
    result = read(source, lambda identity: authorized.append(identity) or True)
    assert len(authorized) == 2
    assert result['job']['status'] == 'RUNNING'
    assert [page['status'] for page in result['pages']] == ['SUCCEEDED', 'PENDING']
    assert result['units'][1]['parent_unit_id'] == 'u1'
    assert all('local_image_path' not in page and 'raw_text' not in page for page in result['pages'])


def test_rejects_stale_versions_and_duplicate_pages(source):
    with pytest.raises(DomainError, match='SOURCE_VERSION_MUTATED'):
        read(source, source_hash='b' * 64)
    with source.begin() as conn:
        conn.execute(text("INSERT INTO ocr_page_tasks VALUES ('p3', 'j1', 1, 'COMPLETED', 0, NULL, NULL, 'private')"))
    with pytest.raises(DomainError, match='SOURCE_OCR_PAGE_CONFLICT'):
        read(source)


def test_rejects_cyclic_units(source):
    with source.begin() as conn:
        conn.execute(text("UPDATE content_units SET parent_unit_id = 'u2' WHERE id = 'u1'"))
    with pytest.raises(DomainError, match='SOURCE_OCR_UNIT_CONFLICT'):
        read(source)


def test_denied_or_revoked_source_mapping_fails_closed(source):
    with pytest.raises(DomainError, match='SOURCE_OCR_UNAUTHORIZED'):
        read(source, lambda _: False)
    answers = iter([True, False])
    with pytest.raises(DomainError, match='SOURCE_OCR_UNAUTHORIZED'):
        read(source, lambda _: next(answers))
    with source.begin() as conn:
        conn.execute(text("UPDATE resource_versions SET lifecycle_status = 'REJECTED' WHERE id = 'v1'"))
    with pytest.raises(DomainError, match='SOURCE_OCR_LIFECYCLE_BLOCKED'):
        read(source)
