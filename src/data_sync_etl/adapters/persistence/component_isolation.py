"""Fail-closed physical PostgreSQL database identity comparison."""
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

_DATABASE_ID = text(
    'SELECT (pg_control_system()).system_identifier, '
    '(SELECT oid FROM pg_database WHERE datname = current_database())'
)


def postgres_component_is_isolated(component, master):
    """Require evidence that two live PostgreSQL connections use different DBs.

    PostgreSQL cluster system identifier + database OID avoid trusting hostnames
    or credentials as identity. If either endpoint cannot provide the evidence,
    the caller must keep the component disabled.
    """
    if component.dialect.name != 'postgresql' or master.dialect.name != 'postgresql':
        return False
    try:
        component_identity = component.execute(_DATABASE_ID).one()
        master_identity = master.execute(_DATABASE_ID).one()
    except (SQLAlchemyError, OSError):
        return False
    if (len(component_identity) != 2 or len(master_identity) != 2
            or any(value is None for value in (*component_identity, *master_identity))):
        return False
    return tuple(component_identity) != tuple(master_identity)
