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
        # Attest the deployed SOURCE contract before allocating anything.  This
        # is read-only and intentionally fails closed when a similarly named
        # table has different columns, nullability, primary key or sequence
        # guard.  No migration or GRANT is attempted here.
        shape = connection.execute(text("""
            SELECT
                bool_and(data_type = 'character varying' AND is_nullable = 'NO') FILTER (WHERE column_name = 'prefix') AS prefix_ok,
                bool_and(data_type = 'character varying' AND is_nullable = 'NO') FILTER (WHERE column_name = 'period') AS period_ok,
                bool_and(data_type = 'integer' AND is_nullable = 'NO') FILTER (WHERE column_name = 'last_seq') AS sequence_ok,
                count(*) FILTER (WHERE column_name IN ('prefix', 'period', 'last_seq')) AS required_columns
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = 'id_counters'
        """)).mappings().one()
        primary_key = connection.scalar(text("""
            SELECT array_agg(att.attname ORDER BY key.ord)::text
            FROM pg_index idx
            CROSS JOIN LATERAL unnest(idx.indkey) WITH ORDINALITY AS key(attnum, ord)
            JOIN pg_attribute att ON att.attrelid = idx.indrelid AND att.attnum = key.attnum
            WHERE idx.indisprimary AND idx.indrelid = to_regclass('id_counters')
        """))
        non_negative = connection.scalar(text("""
            SELECT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = to_regclass('id_counters')
                  AND contype = 'c'
                  AND pg_get_constraintdef(oid) ILIKE '%last_seq%>=%0%'
            )
        """))
        if (shape['required_columns'] != 3 or shape['prefix_ok'] is not True
                or shape['period_ok'] is not True or shape['sequence_ok'] is not True
                or primary_key != '{prefix,period}' or non_negative is not True):
            raise DomainError("Source ID counter schema contract is invalid")
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
