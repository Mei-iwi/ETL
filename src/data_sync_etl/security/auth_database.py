"""Isolated authentication schema. Never uses the business metadata or SOURCE users."""

import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Uuid,
    create_engine,
    delete,
    func,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.exc import SQLAlchemyError

from data_sync_etl.security.auth import AuthError, read_secret

metadata = MetaData()
versions = Table('auth_schema_versions', metadata, Column('version', Integer, primary_key=True))
users = Table('auth_users', metadata,
    Column('id', Uuid(as_uuid=False), primary_key=True),
    Column('email', String(254), nullable=False, unique=True),
    Column('display_name', String(120), nullable=False),
    Column('password_hash', String(255), nullable=False),
    Column('is_active', Boolean, nullable=False), Column('is_locked', Boolean, nullable=False),
    Column('locked_until', Float), Column('failed_login_count', Integer, nullable=False),
    Column('must_change_password', Boolean, nullable=False),
    Column('auth_version', Integer, nullable=False),
    Column('permissions', JSON, nullable=False), Column('resource_ids', JSON, nullable=False),
    Column('last_login_at', Float), Column('password_changed_at', Float, nullable=False),
    Column('created_at', Float, nullable=False), Column('updated_at', Float, nullable=False),
    CheckConstraint('email = lower(trim(email))', name='ck_auth_email_normalized'),
    CheckConstraint("password_hash LIKE '$argon2id$%'", name='ck_auth_password_argon2id'),
    CheckConstraint('auth_version > 0 AND failed_login_count >= 0', name='ck_auth_user_counters'))
roles = Table('auth_roles', metadata,
    Column('id', Integer, primary_key=True), Column('code', String(20), nullable=False, unique=True),
    Column('name', String(80), nullable=False), Column('description', String(255), nullable=False),
    Column('created_at', Float, nullable=False),
    CheckConstraint("code IN ('admin', 'operator', 'viewer')", name='ck_auth_role_code'))
user_roles = Table('auth_user_roles', metadata,
    Column('user_id', Uuid(as_uuid=False), ForeignKey('auth_users.id', ondelete='RESTRICT'), primary_key=True),
    Column('role_id', Integer, ForeignKey('auth_roles.id', ondelete='RESTRICT'), primary_key=True))

ROLE_CODES = ('admin', 'operator', 'viewer')
CAPABILITIES = ('sync', 'process', 'index', 'evaluation')
_lock = threading.RLock()


def email_key(value):
    value = value.strip().casefold()
    if (len(value) > 254 or value.count('@') != 1 or any(c.isspace() for c in value)
            or not value.partition('@')[0] or '.' not in value.partition('@')[2] or value.endswith('.invalid')):
        raise AuthError('AUTH_USER_INPUT_INVALID', 422)
    return value


def database_engine(settings):
    """Only a URL secret; no fallback to Master or a client-supplied connection."""
    try:
        from sqlalchemy.engine import make_url
        raw = read_secret(settings.auth_database_url_file, 'AUTH_DATABASE_SECRET_REQUIRED').decode()
        url = make_url(raw)
        if (url.drivername != 'postgresql+psycopg' or not url.database
                or 'auth' not in url.database.lower() or not url.username or not url.password):
            raise ValueError
        if settings.auth_deployment_profile == 'multi_machine' and url.query.get('sslmode') != 'verify-full':
            raise ValueError
        return create_engine(url, pool_pre_ping=True, hide_parameters=True,
                             connect_args={'connect_timeout': 5, 'options': '-c statement_timeout=10000'})
    except (ValueError, UnicodeError, SQLAlchemyError):
        raise AuthError('AUTH_DATABASE_CONFIG_INVALID', 503) from None


class AuthUserRepository:
    def __init__(self, engine):
        self.engine = engine

    @contextmanager
    def transaction(self, *, lock=False):
        try:
            with _lock, self.engine.begin() as connection:
                if lock and connection.dialect.name == 'postgresql':
                    connection.execute(text('SELECT pg_advisory_xact_lock(27102201)'))
                yield connection
        except SQLAlchemyError:
            raise AuthError('AUTH_DATABASE_UNAVAILABLE', 503) from None

    def check(self):
        with self.transaction() as connection:
            if set(inspect(connection).get_table_names()) != set(metadata.tables):
                raise AuthError('AUTH_DATABASE_SCHEMA_NOT_READY', 503)
            if connection.execute(select(versions.c.version)).scalars().all() != [1]:
                raise AuthError('AUTH_DATABASE_SCHEMA_NOT_READY', 503)
            if set(connection.execute(select(roles.c.code)).scalars()) != set(ROLE_CODES):
                raise AuthError('AUTH_DATABASE_SCHEMA_NOT_READY', 503)

    def check_runtime_role(self):
        with self.transaction() as connection:
            if connection.dialect.name == 'postgresql':
                flags = connection.execute(text('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user')).one()
                if any(flags):
                    raise AuthError('AUTH_DATABASE_PRIVILEGED_ROLE_FORBIDDEN', 503)

    def migrate(self):
        """Version 1, owner CLI only. Startup never calls this method."""
        with self.transaction(lock=True) as connection:
            actual = set(inspect(connection).get_table_names())
            if actual:
                if actual != set(metadata.tables):
                    raise AuthError('AUTH_MIGRATION_NONEMPTY_DATABASE', 409)
                if connection.execute(select(versions.c.version)).scalars().all() != [1]:
                    raise AuthError('AUTH_DATABASE_SCHEMA_NOT_READY', 503)
                if set(connection.execute(select(roles.c.code)).scalars()) != set(ROLE_CODES):
                    raise AuthError('AUTH_DATABASE_SCHEMA_NOT_READY', 503)
                return False
            metadata.create_all(connection)
            connection.execute(versions.insert().values(version=1))
            for index, code in enumerate(ROLE_CODES, 1):
                connection.execute(roles.insert().values(id=index, code=code, name=code.title(),
                    description='ETL authentication role: ' + code, created_at=time.time()))
        return True

    def _user(self, connection, row):
        if row is None:
            return None
        result = dict(row)
        result['roles'] = list(connection.execute(select(roles.c.code).join(user_roles)
            .where(user_roles.c.user_id == result['id']).order_by(roles.c.code)).scalars())
        return result

    def get(self, *, user_id=None, email=None):
        with self.transaction() as connection:
            where = users.c.id == user_id if user_id is not None else users.c.email == email
            return self._user(connection, connection.execute(select(users).where(where)).mappings().first())

    def list(self, page=1, size=20):
        with self.transaction() as connection:
            total = connection.execute(select(func.count()).select_from(users)).scalar_one()
            rows = connection.execute(select(users).order_by(users.c.created_at, users.c.id)
                .offset((page - 1) * size).limit(size)).mappings()
            return [public_user(self._user(connection, row)) for row in rows], total

    def _roles(self, connection, user_id, codes):
        connection.execute(delete(user_roles).where(user_roles.c.user_id == user_id))
        for role_id in connection.execute(select(roles.c.id).where(roles.c.code.in_(codes))).scalars():
            connection.execute(user_roles.insert().values(user_id=user_id, role_id=role_id))

    def _last_admin(self, connection, user_id, values, role_codes=None):
        current = self._user(connection, connection.execute(select(users).where(users.c.id == user_id)).mappings().first())
        if current is None:
            raise AuthError('AUTH_USER_NOT_FOUND', 404)
        remains = (values.get('is_active', current['is_active']) and not values.get('is_locked', current['is_locked'])
                   and 'admin' in (role_codes if role_codes is not None else current['roles']))
        if 'admin' in current['roles'] and current['is_active'] and not current['is_locked'] and not remains:
            count = connection.execute(select(func.count()).select_from(users.join(user_roles).join(roles))
                .where(roles.c.code == 'admin', users.c.is_active.is_(True), users.c.is_locked.is_(False))).scalar_one()
            if count <= 1:
                raise AuthError('AUTH_LAST_ADMIN_REQUIRED', 409)
        return current

    def _actor(self, connection, actor):
        if actor is None:  # Explicit local maintenance, never supplied by an HTTP body.
            return
        current = self._user(connection, connection.execute(select(users).where(users.c.id == actor.id)).mappings().first())
        if (not current or current['auth_version'] != actor.auth_version or not current['is_active']
                or current['is_locked'] or (current['locked_until'] or 0) > time.time()
                or current['must_change_password'] or 'admin' not in current['roles']):
            raise AuthError('AUTH_FORBIDDEN', 403)

    def create(self, email, display_name, password_hash, role_codes, *, bootstrap=False, actor=None, **grants):
        email = email_key(email)
        with self.transaction(lock=True) as connection:
            self._actor(connection, actor)
            existing = self._user(connection, connection.execute(select(users).where(users.c.email == email)).mappings().first())
            if existing:
                if bootstrap and 'admin' in existing['roles']:
                    return public_user(existing), False
                raise AuthError('AUTH_EMAIL_CONFLICT', 409)
            if bootstrap and connection.execute(select(user_roles.c.user_id).join(roles)
                                                .where(roles.c.code == 'admin')).first():
                raise AuthError('AUTH_ADMIN_ALREADY_EXISTS', 409)
            now = time.time()
            user_id = str(uuid.uuid4())
            connection.execute(users.insert().values(id=user_id, email=email, display_name=display_name,
                password_hash=password_hash, is_active=True, is_locked=False, locked_until=None,
                failed_login_count=0, must_change_password=not bootstrap, auth_version=1,
                permissions=grants.get('permissions', []), resource_ids=grants.get('resource_ids', []),
                last_login_at=None, password_changed_at=now, created_at=now, updated_at=now))
            self._roles(connection, user_id, role_codes)
        return public_user(self.get(user_id=user_id)), True

    def change(self, user_id, values, *, role_codes=None, actor=None, expected_version=None):
        with self.transaction(lock=True) as connection:
            self._actor(connection, actor)
            current = self._last_admin(connection, user_id, values, role_codes)
            if expected_version is not None and current['auth_version'] != expected_version:
                raise AuthError('AUTH_SESSION_EXPIRED')
            changes = {**values, 'updated_at': time.time(), 'auth_version': current['auth_version'] + 1}
            if 'password_hash' in values:
                changes.update(password_changed_at=time.time(), failed_login_count=0, locked_until=None)
            if values.get('is_locked') is False:
                changes.update(failed_login_count=0, locked_until=None)
            connection.execute(update(users).where(users.c.id == user_id).values(**changes))
            if role_codes is not None:
                self._roles(connection, user_id, role_codes)
        return public_user(self.get(user_id=user_id))

    def failed(self, user_id, now, limit, window):
        with self.transaction(lock=True) as connection:
            count = connection.execute(select(users.c.failed_login_count).where(users.c.id == user_id)).scalar_one()
            connection.execute(update(users).where(users.c.id == user_id).values(failed_login_count=count + 1,
                locked_until=now + window if count + 1 >= limit else None))

    def successful(self, user_id, version, now):
        with self.transaction(lock=True) as connection:
            result = connection.execute(update(users).where(users.c.id == user_id, users.c.auth_version == version,
                users.c.is_active.is_(True), users.c.is_locked.is_(False),
                (users.c.locked_until.is_(None) | (users.c.locked_until <= now))).values(last_login_at=now,
                failed_login_count=0, locked_until=None))
            return result.rowcount == 1


def public_user(user):
    return {key: value for key, value in user.items() if key not in ('password_hash', 'auth_version')}


def write_schema_description(path):
    """Inspectable migration artifact without executing SQL against a database."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable
    Path(path).write_text('\n\n'.join(str(CreateTable(table).compile(dialect=postgresql.dialect())).strip() + ';'
        for table in metadata.sorted_tables), encoding='utf-8')
