"""Single admin, same-origin cookies, CSRF and explicitly isolated local demo."""

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import VerificationError

from data_sync_etl.logging import event
from data_sync_etl.security.sessions import SessionStore


class AuthError(Exception):
    def __init__(self, code, status=401, retry_after=None):
        self.code, self.status, self.retry_after = code, status, retry_after
        super().__init__(code)


@dataclass(frozen=True)
class Principal:
    id: str
    role: str
    mode: str
    provenance: str


def origin_parts(value):
    try:
        url = urlsplit(value)
        if (url.scheme not in ('http', 'https') or not url.hostname or url.username
                or url.password or url.path not in ('', '/') or url.query or url.fragment):
            raise ValueError
        return url.scheme, url.hostname.lower(), url.port or (443 if url.scheme == 'https' else 80)
    except (ValueError, TypeError):
        raise ValueError('AUTH_ORIGIN_INVALID') from None


def validate_mode(settings):
    mode = settings.auth_mode
    if mode == 'legacy_local':
        if settings.auth_dev_admin_bypass:
            raise ValueError('AUTH_BYPASS_MODE_INVALID')
        return
    origin = origin_parts(settings.auth_origin)
    local = origin[1] in ('127.0.0.1', 'localhost', '::1')
    if mode == 'single_admin':
        if settings.auth_dev_admin_bypass:
            raise ValueError('AUTH_BYPASS_FORBIDDEN')
        if settings.auth_deployment_profile == 'localhost':
            if not local or settings.auth_public_enabled:
                raise ValueError('AUTH_LOCAL_ORIGIN_REQUIRED')
        elif settings.auth_deployment_profile == 'multi_machine':
            if origin[0] != 'https' or not settings.auth_cookie_secure or not settings.auth_public_enabled:
                raise ValueError('AUTH_HTTPS_REQUIRED')
        else:
            raise ValueError('AUTH_DEPLOYMENT_PROFILE_REQUIRED')
    elif mode == 'local_demo':
        if (not settings.auth_dev_admin_bypass or settings.app_environment != 'local'
                or settings.auth_public_enabled or not local or origin[0] != 'http'
                or settings.auth_deployment_profile != 'localhost'):
            raise ValueError('AUTH_LOCAL_DEMO_ISOLATION_REQUIRED')
    else:
        raise ValueError('AUTH_MODE_INVALID')
    if origin[0] == 'https' and not settings.auth_cookie_secure:
        raise ValueError('AUTH_SECURE_COOKIE_REQUIRED')
    if origin[0] == 'http' and settings.auth_cookie_secure:
        raise ValueError('AUTH_COOKIE_ORIGIN_MISMATCH')


def read_secret(path, code, max_bytes=4096):
    try:
        value = Path(path).read_bytes().strip()
        if not value or len(value) > max_bytes:
            raise ValueError
        return value
    except (OSError, TypeError, ValueError):
        raise ValueError(code) from None


class AuthService:
    def __init__(self, settings, *, clock=time.time):
        validate_mode(settings)
        self.settings, self.clock = settings, clock
        self.origin = origin_parts(settings.auth_origin)
        self.key = read_secret(settings.session_signing_key_file, 'AUTH_SESSION_KEY_REQUIRED')
        self.proxy_key = read_secret(settings.auth_proxy_key_file, 'AUTH_PROXY_KEY_REQUIRED')
        if len(self.key) < 32 or not re.fullmatch(b'[a-fA-F0-9]{64}', self.proxy_key):
            raise ValueError('AUTH_SECRET_FORMAT_INVALID')
        self.password_hash = b''
        self.hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
        if settings.auth_mode == 'single_admin':
            self.password_hash = read_secret(settings.admin_password_hash_file, 'AUTH_PASSWORD_HASH_REQUIRED')
            try:
                params = extract_parameters(self.password_hash.decode('ascii'))
                if (params.type is not Type.ID or params.version != 19
                        or not 19456 <= params.memory_cost <= 262144 or not 2 <= params.time_cost <= 10
                        or not 1 <= params.parallelism <= 8 or params.salt_len < 16 or params.hash_len < 16):
                    raise ValueError
            except (ValueError, UnicodeError):
                raise ValueError('AUTH_PASSWORD_HASH_INVALID') from None
            if (not settings.admin_login_email or len(settings.admin_login_email) > 254
                    or '@' not in settings.admin_login_email or settings.admin_login_email.endswith('.invalid')):
                raise ValueError('AUTH_ADMIN_EMAIL_REQUIRED')
        epoch = self.digest(settings.admin_login_email.encode() + b':' + self.password_hash)
        self.store = SessionStore(settings.auth_state_path, epoch)
        prefix = '__Host-' if settings.auth_cookie_secure else ''
        self.cookie_name = prefix + 'etl_session'
        self.csrf_cookie = prefix + 'etl_csrf_seed'

    def digest(self, value):
        return hmac.new(self.key, value, hashlib.sha256).hexdigest()

    def provenance(self, request, *, unsafe=False):
        if any(k == 'forwarded' or k.startswith('x-forwarded-') or k == 'x-real-ip' for k in request.headers):
            raise AuthError('AUTH_PROXY_UNTRUSTED', 403)
        supplied = request.headers.get('x-etl-proxy-key', '').encode()
        if not hmac.compare_digest(supplied, self.proxy_key):
            raise AuthError('AUTH_PROXY_UNTRUSTED', 403)
        try:
            host = origin_parts(self.origin[0] + '://' + request.headers.get('host', ''))
            if host != self.origin:
                raise ValueError
        except ValueError:
            raise AuthError('AUTH_HOST_REJECTED', 403) from None
        origin = request.headers.get('origin')
        referer = request.headers.get('referer')
        if request.headers.get('sec-fetch-site') == 'cross-site':
            raise AuthError('AUTH_ORIGIN_REJECTED', 403)
        if origin:
            try:
                valid = origin_parts(origin) == self.origin
            except ValueError:
                valid = False
            if not valid:
                raise AuthError('AUTH_ORIGIN_REJECTED', 403)
        elif unsafe:
            try:
                parsed = urlsplit(referer or '')
                valid = origin_parts(f'{parsed.scheme}://{parsed.netloc}') == self.origin
            except ValueError:
                valid = False
            if not valid:
                raise AuthError('AUTH_ORIGIN_REQUIRED', 403)

    def csrf_seed(self):
        return f'{int(self.clock())}.{secrets.token_urlsafe(32)}'

    def csrf_token(self, seed):
        return self.digest(b'csrf:' + seed.encode())

    def check_csrf(self, request):
        seed = request.cookies.get(self.csrf_cookie, '')
        supplied = request.headers.get('x-csrf-token', '')
        try:
            stamp = int(seed.partition('.')[0])
            valid_age = 0 <= self.clock() - stamp <= self.settings.auth_remember_seconds
        except ValueError:
            valid_age = False
        if (not valid_age or len(seed) > 256 or not supplied
                or not hmac.compare_digest(supplied.encode(), self.csrf_token(seed).encode())):
            raise AuthError('AUTH_CSRF_INVALID', 403)

    def principal(self, request, *, touch=True):
        if self.settings.auth_mode == 'local_demo':
            self.provenance(request)
            return Principal('local-demo-admin', 'ADMIN', 'local_demo', 'isolated-gateway')
        token = request.cookies.get(self.cookie_name, '')
        if not token:
            raise AuthError('AUTH_LOGIN_REQUIRED')
        if len(token) > 128 or not self.store.get(self.digest(token.encode()), self.clock(), touch):
            raise AuthError('AUTH_SESSION_EXPIRED')
        return Principal('single-admin', 'ADMIN', 'single_admin', 'server-session')

    def live(self, request):
        try:
            self.principal(request, touch=False)
            return True
        except AuthError:
            return False

    def login(self, request, email, password, remember=False):
        if self.settings.auth_mode != 'single_admin':
            raise AuthError('AUTH_LOGIN_NOT_AVAILABLE', 403)
        if not self.store.active():
            raise AuthError('AUTH_STATE_RESTART_REQUIRED', 503)
        peer = request.client.host if request.client else 'unknown'
        now = self.clock()
        for bucket in ('account', 'source:' + self.digest(peer.encode())):
            wait = self.store.rate(bucket, now, self.settings.auth_login_limit, self.settings.auth_login_window_seconds)
            if wait:
                event(stage='authentication', action='THROTTLED')
                raise AuthError('AUTH_LOGIN_THROTTLED', 429, wait)
        try:
            password_ok = self.hasher.verify(self.password_hash, password)
        except (VerificationError, ValueError):
            password_ok = False
        identity_ok = hmac.compare_digest(email.strip().casefold().encode(), self.settings.admin_login_email.casefold().encode())
        if not password_ok or not identity_ok:
            event(stage='authentication', action='LOGIN_REJECTED')
            raise AuthError('AUTH_INVALID_CREDENTIALS')
        token = secrets.token_urlsafe(32)
        old = request.cookies.get(self.cookie_name)
        absolute = self.settings.auth_remember_seconds if remember else self.settings.auth_absolute_seconds
        idle = min(self.settings.auth_remember_idle_seconds, absolute) if remember else self.settings.auth_idle_seconds
        if not self.store.create(self.digest(token.encode()), now, absolute, idle,
                                 self.digest(old.encode()) if old else None):
            raise AuthError('AUTH_SESSION_CAPACITY', 503)
        event(stage='authentication', action='LOGIN_SUCCEEDED')
        return token, absolute

    def cookie_options(self):
        return {'httponly': True, 'secure': self.settings.auth_cookie_secure, 'samesite': 'strict', 'path': '/'}
