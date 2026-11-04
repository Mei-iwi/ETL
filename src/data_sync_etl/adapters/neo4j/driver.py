"""Lazy bounded driver; errors crossing this boundary never include credentials."""

from contextlib import contextmanager
from threading import Lock


class GraphUnavailable(RuntimeError):
    def __init__(self, code='GRAPH_UNAVAILABLE'):
        super().__init__(code)


class GraphDriver:
    def __init__(self, settings):
        self.settings = settings
        self._driver = None
        self._lock = Lock()

    def _open(self):
        if not self.settings.neo4j_enabled:
            raise GraphUnavailable('GRAPH_DISABLED')
        with self._lock:
            if self._driver is None:
                try:
                    from neo4j import GraphDatabase
                    s = self.settings
                    uri = s.neo4j_uri_docker if s.neo4j_runtime == 'docker' else s.neo4j_uri_host
                    self._driver = GraphDatabase.driver(
                        uri, auth=(s.neo4j_user, s.neo4j_password),
                        connection_timeout=s.neo4j_connect_timeout_seconds,
                        connection_acquisition_timeout=s.neo4j_connect_timeout_seconds,
                        max_transaction_retry_time=s.neo4j_query_timeout_seconds,
                        max_connection_pool_size=4,
                    )
                except Exception:
                    raise GraphUnavailable() from None
        return self._driver

    def preflight(self):
        try:
            self._open().verify_connectivity()
            with self.session() as session:
                from neo4j import Query
                session.run(Query('RETURN 1 AS ok', timeout=self.settings.neo4j_query_timeout_seconds)).consume()
            return {'status': 'GRAPH_CONNECTED'}
        except GraphUnavailable:
            raise
        except Exception:
            raise GraphUnavailable() from None

    @contextmanager
    def session(self, *, write=False):
        from neo4j import READ_ACCESS, WRITE_ACCESS
        try:
            with self._open().session(database=self.settings.neo4j_database,
                                      default_access_mode=WRITE_ACCESS if write else READ_ACCESS) as s:
                yield s
        except GraphUnavailable:
            raise
        except Exception:
            raise GraphUnavailable() from None

    def close(self):
        with self._lock:
            if self._driver is not None:
                self._driver.close()
                self._driver = None
