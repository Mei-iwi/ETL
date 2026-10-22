"""Database identities with existing cookie/CSRF/session provenance checks."""

import secrets
import time

from argon2 import Type, extract_parameters
from argon2.exceptions import VerificationError

from data_sync_etl.security.auth import AuthError, AuthService, Principal
from data_sync_etl.security.auth_database import AuthUserRepository, database_engine, email_key


class DatabaseAuthService(AuthService):
    def __init__(self, settings, *, repository=None, clock=time.time):
        super().__init__(settings, clock=clock)
        self.repository = repository or AuthUserRepository(database_engine(settings))
        self.repository.check()
        if repository is None:
            self.repository.check_runtime_role()
        self.dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))

    def principal(self, request, *, touch=True):
        token = request.cookies.get(self.cookie_name, '')
        if not token:
            raise AuthError('AUTH_LOGIN_REQUIRED')
        session = self.store.get(self.digest(token.encode()), self.clock(), touch) if len(token) <= 128 else None
        if not session or 'user_id' not in session:
            raise AuthError('AUTH_SESSION_EXPIRED')
        user = self.repository.get(user_id=session['user_id'])
        if (not user or not user['is_active'] or user['is_locked']
                or (user['locked_until'] or 0) > self.clock()
                or user['auth_version'] != session['auth_version'] or not user['roles']):
            raise AuthError('AUTH_SESSION_EXPIRED')
        role = next(code for code in ('admin', 'operator', 'viewer') if code in user['roles'])
        return Principal(user['id'], role.upper(), 'db_users', 'database-session', tuple(user['roles']),
            user['display_name'], user['email'], user['must_change_password'],
            tuple(user['permissions']), tuple(user['resource_ids']), user['auth_version'])

    def login(self, request, email, password, remember=False):
        if not self.store.active():
            raise AuthError('AUTH_STATE_RESTART_REQUIRED', 503)
        try:
            key = email_key(email)
        except AuthError:
            key = email.strip().casefold()[:254]
        now = self.clock()
        peer = request.client.host if request.client else 'unknown'
        for bucket, limit in [('account:' + self.digest(key.encode()), self.settings.auth_login_limit),
                              ('source:' + self.digest(peer.encode()), self.settings.auth_login_limit * 10)]:
            wait = self.store.rate(bucket, now, limit, self.settings.auth_login_window_seconds)
            if wait:
                raise AuthError('AUTH_LOGIN_THROTTLED', 429, wait)
        user = self.repository.get(email=key)
        encoded = user['password_hash'] if user else self.dummy_hash
        try:
            params = extract_parameters(encoded)
            safe = (params.type == Type.ID and params.version == 19 and 19456 <= params.memory_cost <= 262144
                    and 2 <= params.time_cost <= 10 and 1 <= params.parallelism <= 8
                    and params.hash_len >= 16 and params.salt_len >= 16)
        except (ValueError, TypeError):
            safe = False
        try:
            valid = self.hasher.verify(encoded if safe else self.dummy_hash, password) and safe
        except (VerificationError, ValueError):
            valid = False
        if (not valid or not user or not user['is_active'] or user['is_locked']
                or (user['locked_until'] or 0) > now or not user['roles']):
            if user and not valid:
                self.repository.failed(user['id'], now, self.settings.auth_login_limit, self.settings.auth_login_window_seconds)
            raise AuthError('AUTH_INVALID_CREDENTIALS')
        if not self.repository.successful(user['id'], user['auth_version'], now):
            raise AuthError('AUTH_INVALID_CREDENTIALS')
        token = secrets.token_urlsafe(32)
        old = request.cookies.get(self.cookie_name)
        absolute = self.settings.auth_remember_seconds if remember else self.settings.auth_absolute_seconds
        idle = min(self.settings.auth_remember_idle_seconds, absolute) if remember else self.settings.auth_idle_seconds
        if not self.store.create(self.digest(token.encode()), now, absolute, idle,
                self.digest(old.encode()) if old else None, user_id=user['id'], auth_version=user['auth_version']):
            raise AuthError('AUTH_SESSION_CAPACITY', 503)
        return token, absolute

    def change_password(self, request, old_password, new_password):
        principal = self.principal(request)
        wait = self.store.rate('change:' + principal.id, self.clock(), self.settings.auth_login_limit,
                               self.settings.auth_login_window_seconds)
        if wait:
            raise AuthError('AUTH_LOGIN_THROTTLED', 429, wait)
        user = self.repository.get(user_id=principal.id)
        try:
            valid = self.hasher.verify(user['password_hash'], old_password)
        except (VerificationError, ValueError):
            valid = False
        if not valid:
            raise AuthError('AUTH_INVALID_CREDENTIALS')
        self.repository.change(principal.id, {'password_hash': self.hasher.hash(new_password), 'must_change_password': False},
                               expected_version=principal.auth_version)
        request.state.auth_change_completed = True
