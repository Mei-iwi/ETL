from sqlalchemy import create_engine, inspect

from data_sync_etl.db.models import Base
from data_sync_etl.db.ocr_component_models import (
    ComponentJobRef,
    ComponentPageRef,
    ComponentReferenceBase,
    ComponentUnitRef,
)


def test_dormant_component_import_does_not_expand_master_schema():
    forbidden = {
        'master_component_ocr_jobs',
        'master_component_ocr_pages',
        'master_component_content_units',
    }
    assert not forbidden.intersection(Base.metadata.tables)
    assert set(ComponentReferenceBase.metadata.tables) == forbidden
    assert all(model.metadata is not Base.metadata for model in (
        ComponentJobRef, ComponentPageRef, ComponentUnitRef,
    ))
    # Verify the consequence, not just registry identity, on an isolated DB.
    engine = create_engine('sqlite:///:memory:')
    try:
        Base.metadata.create_all(engine)
        assert not forbidden.intersection(inspect(engine).get_table_names())
    finally:
        engine.dispose()
