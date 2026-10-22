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

    def create(self, token, now, absolute, idle, previous=None):
        with self.connect() as db:
            if not self._current(db):
                return False
            db.execute('DELETE FROM auth_sessions WHERE expires<=? OR seen+idle<=?', (now, now))
            if previous:
                db.execute('DELETE FROM auth_sessions WHERE token=?', (previous,))
            if db.execute('SELECT COUNT(*) FROM auth_sessions').fetchone()[0] >= 256:
                return False
            db.execute('INSERT INTO auth_sessions VALUES (?,?,?,?,?)', (token, now, now, now + absolute, idle))
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
            return {'created': row[0], 'expires': row[2]}

    def revoke(self, token):
        with self.connect() as db:
            if self._current(db):
                db.execute('DELETE FROM auth_sessions WHERE token=?', (token,))

    def _current(self, db):
        return db.execute('SELECT epoch FROM auth_meta WHERE id=1').fetchone() == (self.epoch,)

    def active(self):
        with self.connect() as db:
            return self._current(db)
