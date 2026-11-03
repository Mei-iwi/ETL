"""Component migrations require an externally verified isolated connection."""
from alembic import context
from data_sync_etl.adapters.persistence.component_isolation import postgres_component_is_isolated
from data_sync_etl.adapters.persistence.component_ocr_lease import (
    component_page_leases,  # noqa: F401
)
from data_sync_etl.adapters.persistence.component_ocr_outbox import component_metadata
from data_sync_etl.adapters.persistence.component_ocr_units import (  # noqa: F401
    component_postprocess_manifests,
    component_unit_provenance,
)

config = context.config
connection = config.attributes.get('connection')
verify_isolated = config.attributes.get('verify_isolated')
if connection is None or not callable(verify_isolated) or not verify_isolated(connection):
    raise RuntimeError('COMPONENT_MIGRATION_ISOLATION_NOT_VERIFIED')
if connection.dialect.name == 'postgresql':
    master = config.attributes.get('master_connection')
    protected = config.attributes.get('protected_source_connections')
    if (master is None or not isinstance(protected, (list, tuple))
            or not postgres_component_is_isolated(connection, master)
            or any(not postgres_component_is_isolated(connection, item) for item in protected)):
        raise RuntimeError('COMPONENT_MIGRATION_ISOLATION_NOT_VERIFIED')
elif connection.dialect.name == 'sqlite':
    if connection.engine.url.database not in (None, ':memory:'):
        raise RuntimeError('COMPONENT_MIGRATION_ISOLATION_NOT_VERIFIED')
else:
    raise RuntimeError('COMPONENT_MIGRATION_ISOLATION_NOT_VERIFIED')

context.configure(connection=connection, target_metadata=component_metadata,
                  version_table='alembic_component_version', compare_type=True)
with context.begin_transaction():
    context.run_migrations()
