import hashlib
import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select

from data_sync_etl.adapters.persistence.component_ocr_projection import (
    ComponentOCRProjector,
    ProjectionError,
)
from data_sync_etl.db.ocr_component_models import (
    ComponentJobRef,
    ComponentPageRef,
    ComponentUnitRef,
)
from data_sync_etl.domain.component_ocr_probe import probe_ocr


@pytest.fixture
def db():
    engine = create_engine('sqlite:///:memory:')
    for model in (ComponentJobRef, ComponentPageRef, ComponentUnitRef):
        model.__table__.create(engine)
    with engine.begin() as conn:
        yield conn
    engine.dispose()


def job(**changes):
    return dict(namespace='source-a', source_job_id='job-1', generation=1,
                source_version_id='version-1', master_version_id='master-v',
                master_resource_id='master-r', source_hash='a' * 64,
                processing_fingerprint='b' * 64, status='RUNNING', total_pages=2,
                completed_pages=0, failed_pages=0, revision=1, tombstone=False) | changes


def page(**changes):
    return dict(namespace='source-a', source_task_id='task-1', generation=1,
                source_job_id='job-1', master_version_id='master-v', page_num=1,
                status='SUCCEEDED', retry_count=0, error_code=None, verified=True,
                revision=1, tombstone=False) | changes


def unit(**changes):
    return dict(namespace='source-a', source_unit_id='unit-1', generation=1,
                source_version_id='version-1', master_version_id='master-v',
                master_resource_id='master-r', parent_unit_id=None, unit_type='SECTION',
                sequence_no=1, page_from=1, page_to=2, review_status='APPROVED',
                retrieval_eligible=True, text_checksum='c' * 64, verified=True,
                revision=1, tombstone=False) | changes


def test_all_three_replicas_replay_and_source_scoping(db):
    projector = ComponentOCRProjector(db, lambda kind, data: True)
    for kind, make in (('job', job), ('page', page), ('unit', unit)):
        assert projector.apply(kind, make())
        assert projector.apply(kind, make())
        assert projector.apply(kind, make(namespace='source-b'))
    for model in (ComponentJobRef, ComponentPageRef, ComponentUnitRef):
        assert len(db.execute(select(model.__table__)).all()) == 2
        assert not {'raw_text', 'normalized_text', 'text', 'local_image_path'} & set(model.__table__.c.keys())


def test_revision_and_identity_are_fenced(db):
    projector = ComponentOCRProjector(db, lambda kind, data: True)
    projector.apply('job', job(revision=2, completed_pages=1))
    assert not projector.apply('job', job(revision=1))
    current = db.execute(select(ComponentJobRef.__table__)).mappings().one()
    assert current['completed_pages'] == 1
    with pytest.raises(ProjectionError, match='REVISION_CONFLICT'):
        projector.apply('job', job(revision=2, completed_pages=2))
    with pytest.raises(ProjectionError, match='IDENTITY_CONFLICT'):
        projector.apply('job', job(revision=3, master_version_id='other'))
    assert db.execute(select(ComponentJobRef.__table__)).mappings().one()['revision'] == 2


def test_payload_and_authority_rejected_before_write(db):
    projector = ComponentOCRProjector(db, lambda kind, data: False)
    with pytest.raises(ProjectionError, match='IDENTITY_NOT_AUTHORIZED'):
        projector.apply('job', job())
    with pytest.raises(ProjectionError, match='INVALID_FIELDS'):
        projector.apply('page', page(raw_text='secret'))
    with pytest.raises(ProjectionError, match='INVALID_ERROR_CODE'):
        projector.apply('page', page(error_code='/private/path'))
    assert db.execute(select(ComponentJobRef.__table__)).all() == []


def test_lineage_requires_parent_in_same_namespace_version_and_generation(db):
    projector = ComponentOCRProjector(db, lambda kind, data: True)
    with pytest.raises(ProjectionError, match='PARENT_NOT_READY'):
        projector.apply('page', page())
    projector.apply('job', job())
    projector.apply('page', page())
    with pytest.raises(ProjectionError, match='PARENT_NOT_READY'):
        projector.apply('unit', unit(source_unit_id='child', parent_unit_id='parent'))
    projector.apply('unit', unit(source_unit_id='parent'))
    assert projector.apply('unit', unit(source_unit_id='child', parent_unit_id='parent'))
    with pytest.raises(ProjectionError, match='PARENT_NOT_READY'):
        projector.apply('unit', unit(source_unit_id='parent', parent_unit_id='child', revision=2))
    with pytest.raises(ProjectionError, match='PARENT_NOT_READY'):
        projector.apply('unit', unit(source_unit_id='child-b', parent_unit_id='parent', namespace='source-b'))


def test_probe_empty_page_is_valid_but_missing_or_corrupt_is_not():
    content = ''
    checksum = hashlib.sha256(content.encode()).hexdigest()
    identity = dict(namespace='source-a', version_id='version-1', generation=1,
                    source_hash='a' * 64, fingerprint='b' * 64)
    manifest = dict(**identity, total_pages=2, verified=True)
    args = dict(authorized=True, available=True, namespace='source-a', version_id='version-1',
                generation=1, expected_hash='a' * 64,
                expected_fingerprint='b' * 64, manifest=manifest)
    first = dict(**identity, page_num=1, raw_text=content, checksum=checksum, verified=True)
    second = dict(first, page_num=2)
    assert probe_ocr(**args, pages={1: first}).missing_pages == (2,)
    assert probe_ocr(**args, pages={1: first, 2: second}).status == 'COMPLETE_VERIFIED'
    assert probe_ocr(**args, pages={1: first, 2: dict(second, checksum='c' * 64)}).missing_pages == (2,)
    assert probe_ocr(**args, pages={1: first, 2: dict(second, namespace='other')}).missing_pages == (2,)
    assert probe_ocr(**(args | {'available': False}), pages={}).status == 'INACCESSIBLE'
    assert probe_ocr(**(args | {'authorized': False}), pages={}).status == 'UNAUTHORIZED'
    assert probe_ocr(**(args | {'expected_hash': 'c' * 64}), pages={}).status == 'STALE'


@pytest.mark.parametrize('approval', [None, 'unapproved'])
def test_disabled_migration_preserves_master_0020(approval, monkeypatch):
    # MASTER-only deployment explicitly retired the experimental 0021 schema.
    # Protect the same isolation/no-unintended-DDL requirement at revision0020
    # instead of requiring the disabled migration to create undeployed tables.
    from conftest import migrate

    if approval is None:
        monkeypatch.delenv('ETL_APPROVE_COMPONENT_OCR_0021', raising=False)
    else:
        monkeypatch.setenv('ETL_APPROVE_COMPONENT_OCR_0021', approval)
    path = Path(__file__).parents[2] / 'alembic/versions/0021_component_ocr_read_models.py'
    spec = importlib.util.spec_from_file_location('isolated_ocr_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine('sqlite:///:memory:')
    migrate(engine, revision='0020')
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        before = set(inspect(conn).get_table_names())
        with pytest.raises(RuntimeError, match='Revision 0021 is disabled'):
            module.upgrade()
        assert set(inspect(conn).get_table_names()) == before
        assert not {'master_component_ocr_jobs', 'master_component_ocr_pages',
                    'master_component_content_units'}.intersection(before)
        with pytest.raises(RuntimeError, match='transactional PostgreSQL MASTER'):
            module.downgrade()
        assert set(inspect(conn).get_table_names()) == before
    engine.dispose()
