"""Configured admin and verified MASTER roles, gated by ADMIN_ENABLED."""

import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import UTC

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from data_sync_etl.db.models import MasterLearningResource, MasterUser, SyncRun
from data_sync_etl.logging import event
from data_sync_etl.security.auth import AuthError, AuthService, Principal


def verify_pbkdf2(encoded, password, salt_encoding='auto'):
    """Observed SOURCE contract: PBKDF2-HMAC-SHA256 with a hex digest, no write-back."""
    if not isinstance(encoded, str) or len(encoded) > 255:
        return False
    match = re.fullmatch(r'pbkdf2_sha256\$([1-9][0-9]{3,6})\$([0-9a-f]{16,128})\$([0-9a-f]{64})', encoded)
    if not match or not 10000 <= int(match[1]) <= 2000000:
        return False
    try:
        if salt_encoding not in ('text', 'hex', 'auto'):
            return False
        salts = [match[2].encode('ascii')] if salt_encoding in ('text', 'auto') else []
        if salt_encoding == 'hex' or (salt_encoding == 'auto' and len(match[2]) % 2 == 0):
            salts.append(bytes.fromhex(match[2]))
        valid = False
        for salt in salts:  # Both legacy encodings have the same bounded cryptographic verification.
            derived = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, int(match[1]), dklen=32)
            valid |= hmac.compare_digest(derived.hex(), match[3])
        return valid
    except (ValueError, UnicodeError):
        return False


class MasterIdentityReader:
    def __init__(self, settings, *, engine=None):
        self.settings = settings
        if engine is None and settings.auth_deployment_profile == 'multi_machine':
            if make_url(settings.database_url).query.get('sslmode') != 'verify-full':
                raise AuthError('AUTH_MASTER_TLS_REQUIRED', 503)
        self.engine = engine or create_engine(settings.database_url, hide_parameters=True, pool_pre_ping=True,
            connect_args={'connect_timeout': 5, 'options': '-c default_transaction_read_only=on -c statement_timeout=5000'})
        if engine is None and self.engine.dialect.name != 'postgresql':
            raise AuthError('AUTH_MASTER_POSTGRES_REQUIRED', 503)

    def read(self, *, email=None, user_id=None, now=None):
        now = time.time() if now is None else now
        try:
            isolation = 'REPEATABLE READ' if self.engine.dialect.name == 'postgresql' else 'SERIALIZABLE'
            with self.engine.connect().execution_options(isolation_level=isolation, postgresql_readonly=True) as connection:
                with connection.begin():
                    stamp = connection.scalar(select(func.max(SyncRun.finished_at)).where(
                        SyncRun.stream_name == 'users', SyncRun.status == 'COMPLETED'))
                    if stamp is None:
                        raise AuthError('AUTH_MASTER_STALE', 503)
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=UTC)
                    age = now - stamp.timestamp()
                    if age < -30 or age > self.settings.auth_master_freshness_seconds:
                        raise AuthError('AUTH_MASTER_STALE', 503)
                    fields = [MasterUser.id, MasterUser.email, MasterUser.full_name, MasterUser.hashed_password,
                        MasterUser.is_active, MasterUser.deleted_at, MasterUser.email_verified_at,
                        MasterUser.role_id, MasterUser.role_code, MasterUser.role_is_active, MasterUser.role_deleted_at,
                        MasterUser.source_origin, MasterUser.source_missing_at]
                    predicate = MasterUser.id == user_id if user_id is not None else func.lower(func.trim(MasterUser.email)) == email
                    rows = connection.execute(select(*fields).where(predicate).limit(2)).mappings().all()
                    if len(rows) != 1:
                        return None
                    user = dict(rows[0])
                    owned = connection.scalars(select(MasterLearningResource.id).where(
                        MasterLearningResource.created_by == user['id'],
                        MasterLearningResource.is_active.is_(True), MasterLearningResource.deleted_at.is_(None),
                        MasterLearningResource.source_missing_at.is_(None),
                        MasterLearningResource.publication_status == 'PUBLISHED').limit(1001)).all()
                    # Synced users can access only resources they created. Legacy
                    # explicit grants must never expand ownership-based access.
                    grants = set(owned)
                    if len(grants) > 1000 or any(not isinstance(value, str) or not 1 <= len(value) <= 30 for value in grants):
                        raise AuthError('AUTH_MASTER_GRANTS_INVALID', 503)
                    user['resource_ids'] = sorted(grants)
                    return user
        except SQLAlchemyError:
            raise AuthError('AUTH_MASTER_UNAVAILABLE', 503) from None


def eligible(user):
    return bool(user and user['is_active'] and user['deleted_at'] is None
        and user['source_missing_at'] is None and user['source_origin'] == 'POSTGRESQL'
        and user['role_is_active'] and user['role_deleted_at'] is None and user['email_verified_at'] is not None)


class MasterAuthService(AuthService):
    def __init__(self, settings, *, reader=None, clock=time.time):
        super().__init__(settings, clock=clock)
        self.reader = reader or MasterIdentityReader(settings)
        salt = secrets.token_hex(12)
        dummy = hashlib.pbkdf2_hmac('sha256', secrets.token_bytes(32), salt.encode(), 120000).hex()
        self.dummy_hash = f'pbkdf2_sha256$120000${salt}${dummy}'

    def fingerprint(self, user):
        return self.digest(json.dumps(user, sort_keys=True, default=str).encode())

    def login(self, request, email, password, remember=False, *, provider=None):
        # provider is accepted only for old clients; it cannot select an identity or role.
        if not self.store.active():
            raise AuthError('AUTH_STATE_RESTART_REQUIRED', 503)
        now, normalized = self.clock(), email.strip().casefold()
        peer = request.client.host if request.client else 'unknown'
        for bucket, limit in [('master-account:' + self.digest(normalized.encode()), self.settings.auth_login_limit),
                              ('master-source:' + self.digest(peer.encode()), self.settings.auth_login_limit * 10)]:
            wait = self.store.rate(bucket, now, limit, self.settings.auth_login_window_seconds)
            if wait:
                raise AuthError('AUTH_LOGIN_THROTTLED', 429, wait)
        admin_identity = hmac.compare_digest(normalized.encode(), self.settings.admin_login_email.casefold().encode())
        if self.settings.admin_enabled and admin_identity:
            from argon2.exceptions import VerificationError
            try:
                admin_password = self.hasher.verify(self.password_hash, password)
            except (VerificationError, ValueError):
                admin_password = False
            if admin_password:
                return self.issue_session(request, remember)
        user = self.reader.read(email=normalized, now=now)
        encoded = user['hashed_password'] if user else self.dummy_hash
        valid = verify_pbkdf2(encoded, password, self.settings.auth_master_pbkdf2_salt_encoding)
        if not valid or not eligible(user):
            event(stage='authentication', action=('MASTER_LOGIN_PASSWORD_MISMATCH' if user and not valid
                else 'MASTER_LOGIN_STATE_REJECTED' if user else 'MASTER_LOGIN_IDENTITY_NOT_FOUND'))
            raise AuthError('AUTH_INVALID_CREDENTIALS')
        current = self.reader.read(user_id=user['id'], now=self.clock())
        if not eligible(current) or not hmac.compare_digest(self.fingerprint(user), self.fingerprint(current)):
            raise AuthError('AUTH_INVALID_CREDENTIALS')
        return self.issue_session(request, remember, external_user_id=user['id'], external_fingerprint=self.fingerprint(user))

    def principal(self, request, *, touch=True):
        token = request.cookies.get(self.cookie_name, '')
        session = self.store.get(self.digest(token.encode()), self.clock(), touch) if token and len(token) <= 128 else None
        if not session:
            raise AuthError('AUTH_LOGIN_REQUIRED' if not token else 'AUTH_SESSION_EXPIRED')
        if 'external_user_id' not in session:
            if not self.settings.admin_enabled:
                raise AuthError('AUTH_ADMIN_DISABLED', 403)
            return Principal('single-admin', 'ADMIN', 'master_users', 'configured-admin', ('admin',),
                             'Quản trị viên', self.settings.admin_login_email)
        user = self.reader.read(user_id=session['external_user_id'], now=self.clock())
        if not eligible(user) or not hmac.compare_digest(session['external_fingerprint'], self.fingerprint(user)):
            raise AuthError('AUTH_SESSION_EXPIRED')
        role = 'ADMIN' if self.settings.admin_enabled and user['role_code'] == 'ADMIN' else 'VIEWER'
        return Principal('master:' + user['id'], role, 'master_users', 'master-readonly', (role.lower(),),
                         user['full_name'] or '', user['email'],
                         permissions=('sync', 'process', 'index', 'evaluation') if role == 'ADMIN' else (),
                         resource_ids=tuple(user['resource_ids']))
