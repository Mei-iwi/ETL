"""Serialize only queue admission, never execution or external I/O."""
from hashlib import sha256

from sqlalchemy import text


def admission_lock(session, queue):
    if session.get_bind().dialect.name == 'postgresql':
        key = int.from_bytes(sha256(('etl-admission:' + queue).encode()).digest()[:8],
                             'big', signed=True)
        session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})
    elif session.get_bind().dialect.name == 'sqlite':
        # SQLite has one writer. Take its write reservation before reading capacity.
        connection = session.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
    else:
        raise RuntimeError('Queue admission requires PostgreSQL or SQLite')


def processing_available(session, name):
    if session.get_bind().dialect.name != 'postgresql':
        return True
    # Same namespace as UoW.processing_lock, acquired nonblocking to avoid a
    # lock-order cycle. An expired worker may still hold that execution lock.
    key = int.from_bytes(sha256(('etl-finalize:' + name).encode()).digest()[:8],
                         'big', signed=True)
    return bool(session.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': key}))
