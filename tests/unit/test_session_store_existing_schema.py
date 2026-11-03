from contextlib import contextmanager
from unittest.mock import MagicMock

from data_sync_etl.security.sessions import SessionStore


def test_existing_session_schema_boots_without_ddl(monkeypatch, tmp_path):
    tables = ['auth_meta', 'auth_sessions', 'auth_rate', 'auth_db_bindings', 'auth_external_bindings']
    connection = MagicMock()

    def execute(statement, *args):
        if 'sqlite_master' in statement:
            return [(name,) for name in tables]
        result = MagicMock()
        result.fetchone.return_value = ('existing-epoch',)
        return result

    connection.execute.side_effect = execute

    @contextmanager
    def connect(_self):
        yield connection

    monkeypatch.setattr(SessionStore, 'connect', connect)
    monkeypatch.setattr('data_sync_etl.security.sessions.sqlite3.connect', MagicMock())
    monkeypatch.setattr('data_sync_etl.security.sessions.Path.chmod', lambda *_: None)
    SessionStore(tmp_path / 'existing.sqlite3', 'existing-epoch')
    statements = [call.args[0] for call in connection.execute.call_args_list]
    assert not any(statement.startswith(('CREATE ', 'ALTER ', 'DROP ')) for statement in statements)
    assert not any(statement.startswith('DELETE ') for statement in statements)
    assert any(statement.startswith('SELECT epoch ') for statement in statements)
