import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from data_sync_etl.adapters.persistence.component_ocr_outbox import component_metadata
from data_sync_etl.composition import Container
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError


def test_component_opt_in_fails_closed_before_creating_connections():
    settings = Settings(_env_file=None, ocr_component_pipeline_enabled=True)
    with pytest.raises(DomainError, match='BLOCKED_COMPONENT_PIPELINE_NOT_READY'):
        Container(settings=settings)


def test_component_projection_is_routed_through_actual_container_worker(container):
    class Projection:
        def __init__(self):
            self.calls = 0

        def run_once(self):
            self.calls += 1
            return 1

    projection = Projection()
    configured = Container(container.settings, container.engine, container.documents,
                           component_projection_worker=projection)
    configured.ocr.schedule_authorized_source_job = lambda: False
    configured.ocr.claim = lambda worker_id: None
    assert configured.worker.once('worker-test') is True
    assert projection.calls == 1


def test_component_migration_requires_injected_isolation_verifier():
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        config = Config()
        config.set_main_option('script_location', 'alembic_component')
        config.attributes['connection'] = connection
        with pytest.raises(RuntimeError, match='ISOLATION_NOT_VERIFIED'):
            command.upgrade(config, 'head')
        config.attributes['verify_isolated'] = lambda conn: conn is connection
        command.upgrade(config, 'head')
        assert {'component_ocr_jobs', 'component_ocr_pages', 'component_content_units',
                'component_ocr_outbox', 'alembic_component_version'} <= set(inspect(connection).get_table_names())
        for table in component_metadata.sorted_tables:
            columns = inspect(connection).get_columns(table.name)
            assert {column['name'] for column in columns} == set(table.c.keys())
            assert set(inspect(connection).get_pk_constraint(table.name)['constrained_columns']) == {
                column.name for column in table.primary_key.columns
            }
    engine.dispose()
