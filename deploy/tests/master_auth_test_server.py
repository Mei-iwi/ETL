"""QA fixture only: no SOURCE, Master PostgreSQL, MongoDB or user credentials."""

import hashlib
from pathlib import Path

from data_sync_etl.domain.core import now
from data_sync_etl.main import create_app
from data_sync_etl.security import auth
from data_sync_etl.security.auth import AuthError
from data_sync_etl.security.master_service import MasterAuthService, MasterIdentityReader
from deploy.tests.auth_test_server import container

password = Path('/run/secrets/qa_password').read_text(encoding='ascii')
salt = 'ab12' * 6
digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 120000).hex()
container.sync.run(full=True)
with container.uow() as repo:
    repo.update('master_users', 'user-1', {
        'email': 'master@etl.test', 'hashed_password': f'pbkdf2_sha256$120000${salt}${digest}',
        'source_origin': 'POSTGRESQL', 'is_active': True, 'deleted_at': None, 'source_missing_at': None,
        'role_is_active': True, 'role_deleted_at': None, 'email_verified_at': now(), 'role_code': 'USER',
    })
class QAReader(MasterIdentityReader):
    def read(self, **kwargs):
        if Path('/run/etl/master-stale').exists():
            raise AuthError('AUTH_MASTER_STALE', 503)
        return super().read(**kwargs)


auth.create_auth_service = lambda settings: MasterAuthService(settings,
    reader=QAReader(settings, engine=container.engine))
app = create_app(container)
