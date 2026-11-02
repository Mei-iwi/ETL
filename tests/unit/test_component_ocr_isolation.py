from types import SimpleNamespace

from sqlalchemy.exc import OperationalError

from data_sync_etl.adapters.persistence.component_isolation import postgres_component_is_isolated


class Connection:
    def __init__(self, dialect, identity=None, fail=False):
        self.dialect = SimpleNamespace(name=dialect)
        self.identity = identity
        self.fail = fail

    def execute(self, _statement):
        if self.fail:
            raise OperationalError('private', {}, Exception('private'))
        return SimpleNamespace(one=lambda: self.identity)


def test_database_isolation_requires_live_identity_proof():
    master = Connection('postgresql', (10, 20))
    assert not postgres_component_is_isolated(Connection('postgresql', (10, 20)), master)
    assert postgres_component_is_isolated(Connection('postgresql', (10, 21)), master)
    assert postgres_component_is_isolated(Connection('postgresql', (11, 20)), master)
    assert not postgres_component_is_isolated(Connection('postgresql', (10, None)), master)
    assert not postgres_component_is_isolated(Connection('postgresql', fail=True), master)
    assert not postgres_component_is_isolated(Connection('sqlite', (11, 21)), master)
