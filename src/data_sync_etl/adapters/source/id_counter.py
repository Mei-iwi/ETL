from sqlalchemy import text
from sqlalchemy.engine import Connection

from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.source_ids import validate_counter_key


class PostgreSQLSourceIDCounter:
    """Writer library bound to caller's SOURCE transaction, never used by the reader."""

    def __init__(self, connection: Connection):
        if (
            connection.dialect.name != "postgresql"
            or not connection.in_transaction()
            or connection.get_execution_options().get("isolation_level") == "AUTOCOMMIT"
        ):
            raise DomainError("Source ID allocation requires an explicit PostgreSQL transaction")
        if (
            connection.scalar(text("SELECT to_regclass('alembic_source_version')")) is None
            or connection.scalar(text("SELECT to_regclass('master_learning_resources')"))
            is not None
        ):
            raise DomainError("Source ID counter requires a SOURCE schema, not Master")
        self.connection = connection

    def next_sequence(self, prefix: str, period: str) -> int:
        if (
            not self.connection.in_transaction()
            or self.connection.get_execution_options().get("isolation_level") == "AUTOCOMMIT"
        ):
            raise DomainError("Source ID allocation requires an explicit PostgreSQL transaction")
        validate_counter_key(prefix, period)
        value = self.connection.execute(
            text("""
            INSERT INTO id_counters(prefix,period,last_seq) VALUES (:prefix,:period,1)
            ON CONFLICT(prefix,period) DO UPDATE SET last_seq=id_counters.last_seq+1
            WHERE id_counters.last_seq < 99999
            RETURNING last_seq
        """),
            {"prefix": prefix, "period": period},
        ).scalar_one_or_none()
        if value is None:
            raise DomainError("Source ID sequence exhausted")
        return value
