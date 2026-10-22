"""Single-instance persistent session/rate state; never uses business databases."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path


class SessionStore:
    def __init__(self, path, epoch):
        self.path = Path(path)
        self.epoch = epoch
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        initial = sqlite3.connect(self.path, timeout=5)
        try:
            initial.execute('PRAGMA journal_mode=WAL')
        finally:
            initial.close()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS auth_meta (id INTEGER PRIMARY KEY, epoch TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS auth_sessions (token TEXT PRIMARY KEY, created REAL NOT NULL, seen REAL NOT NULL, expires REAL NOT NULL, idle INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS auth_rate (bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, resets REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS auth_db_bindings (token TEXT PRIMARY KEY REFERENCES auth_sessions(token) ON DELETE CASCADE, user_id TEXT NOT NULL, auth_version INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS auth_external_bindings (token TEXT PRIMARY KEY REFERENCES auth_sessions(token) ON DELETE CASCADE, user_id TEXT NOT NULL, fingerprint TEXT NOT NULL)')
            previous = db.execute('SELECT epoch FROM auth_meta WHERE id=1').fetchone()
            if previous and previous[0] != epoch:
                db.execute('DELETE FROM auth_sessions')
                db.execute('DELETE FROM auth_rate')
            db.execute('INSERT OR REPLACE INTO auth_meta VALUES (1,?)', (epoch,))
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA busy_timeout=5000')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def rate(self, bucket, now, limit, window):
        with self.connect() as db:
            if not self._current(db):
                return window
            db.execute('DELETE FROM auth_rate WHERE resets<=?', (now,))
            row = db.execute('SELECT count,resets FROM auth_rate WHERE bucket=?', (bucket,)).fetchone()
            if row and row[0] >= limit:
                return max(1, int(row[1] - now) + 1)
            db.execute('INSERT OR REPLACE INTO auth_rate VALUES (?,?,?)',
                       (bucket, row[0] + 1 if row else 1, row[1] if row else now + window))
        return 0

    def create(self, token, now, absolute, idle, previous=None, *, user_id=None, auth_version=None,
               external_user_id=None, external_fingerprint=None):
        with self.connect() as db:
            if not self._current(db):
                return False
            db.execute('DELETE FROM auth_sessions WHERE expires<=? OR seen+idle<=?', (now, now))
            if previous:
                db.execute('DELETE FROM auth_sessions WHERE token=?', (previous,))
            if db.execute('SELECT COUNT(*) FROM auth_sessions').fetchone()[0] >= 256:
                return False
            db.execute('INSERT INTO auth_sessions VALUES (?,?,?,?,?)', (token, now, now, now + absolute, idle))
            if user_id is not None:
                db.execute('INSERT INTO auth_db_bindings VALUES (?,?,?)', (token, user_id, auth_version))
            if external_user_id is not None:
                db.execute('INSERT INTO auth_external_bindings VALUES (?,?,?)', (token, external_user_id, external_fingerprint))
        return True

    def get(self, token, now, touch=True):
        with self.connect() as db:
            if not self._current(db):
                return None
            row = db.execute('SELECT created,seen,expires,idle FROM auth_sessions WHERE token=?', (token,)).fetchone()
            if not row or now >= row[2] or now >= row[1] + row[3]:
                if row:
                    db.execute('DELETE FROM auth_sessions WHERE token=?', (token,))
                return None
            if touch:
                db.execute('UPDATE auth_sessions SET seen=? WHERE token=?', (now, token))
            result = {'created': row[0], 'expires': row[2]}
            binding = db.execute('SELECT user_id,auth_version FROM auth_db_bindings WHERE token=?', (token,)).fetchone()
            if binding:
                result.update(user_id=binding[0], auth_version=binding[1])
            external = db.execute('SELECT user_id,fingerprint FROM auth_external_bindings WHERE token=?', (token,)).fetchone()
            if external:
                result.update(external_user_id=external[0], external_fingerprint=external[1])
            return result

    def revoke(self, token):
        with self.connect() as db:
            if self._current(db):
                db.execute('DELETE FROM auth_sessions WHERE token=?', (token,))

    def _current(self, db):
        return db.execute('SELECT epoch FROM auth_meta WHERE id=1').fetchone() == (self.epoch,)

    def active(self):
        with self.connect() as db:
            return self._current(db)
