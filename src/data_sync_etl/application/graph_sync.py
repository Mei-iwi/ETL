"""Independent projection orchestration. No SQL/Mongo writes or OCR dependencies."""

import json
import os
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from data_sync_etl.domain.graph_contract import GraphContractError


def graph_diff(expected, actual):
    return {
        'missing_nodes': sorted(expected.nodes - actual.nodes),
        'extra_nodes': sorted(actual.nodes - expected.nodes),
        'missing_edges': sorted(expected.edges - actual.edges),
        'extra_edges': sorted(actual.edges - expected.edges),
    }


def diff_counts(diff):
    return {key: len(value) for key, value in diff.items()}


@contextmanager
def graph_lock(path):
    """Single host/shared directory scope; OS releases lock on crash.

    Multiple independent writers on other hosts are unsupported in v1. Readers
    see an atomic Neo4j transaction; an incomplete sync never replaces the graph.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('a+b')
    try:
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise GraphContractError('GRAPH_SYNC_BUSY') from None
    try:
        yield
    finally:
        stream.seek(0)
        if os.name == 'nt':
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()


class GraphSynchronizer:
    def __init__(self, reader, projection, state_dir):
        self.reader, self.projection = reader, projection
        self.state_dir = Path(state_dir)

    def _identity(self):
        s = self.projection.driver.settings
        uri = s.neo4j_uri_docker if s.neo4j_runtime == 'docker' else s.neo4j_uri_host
        return {'database_id': self.projection.database_identity(),
                'endpoint_digest': sha256((uri+'\0'+s.neo4j_database).encode()).hexdigest()}

    def _receipt(self, identity):
        return self.state_dir / ('owner-'+identity['database_id']+'.json')

    def claim_empty(self):
        identity = self._identity()
        with graph_lock(self.state_dir / ('sync-'+identity['database_id']+'.lock')):
            current = self.projection.read()
            if current.nodes or current.edges:
                raise GraphContractError('GRAPH_NONEMPTY_OWNERSHIP_UNPROVEN')
            path = self._receipt(identity)
            if path.exists():
                self._verify_owner(identity)
            else:
                path.write_text(json.dumps(identity, sort_keys=True), encoding='utf-8')
        return {'status': 'EMPTY_GRAPH_OWNERSHIP_RECORDED'}

    def _verify_owner(self, identity):
        try:
            path = self._receipt(identity)
            if path.is_symlink() or json.loads(path.read_text(encoding='utf-8')) != identity:
                raise ValueError()
        except (OSError, ValueError):
            raise GraphContractError('GRAPH_OWNERSHIP_UNPROVEN') from None

    def sync(self, *, apply=False, expected_fingerprint=None):
        identity = self._identity()
        with graph_lock(self.state_dir / ('sync-'+identity['database_id']+'.lock')):
            # Construct and validate MASTER input before any graph schema/write.
            expected = self.reader.read()
            before = self.projection.read()
            diff = graph_diff(expected, before)
            summary = {'status': 'DIFF' if any(diff.values()) else 'MATCH',
                       'expected': expected.counts(), 'actual': before.counts(),
                       'diff': diff_counts(diff), 'fingerprint': expected.fingerprint,
                       'writes': 0}
            if not apply:
                return summary
            self._verify_owner(identity)
            if not self.projection.driver.settings.neo4j_sync_enabled:
                raise GraphContractError('GRAPH_SYNC_DISABLED')
            if expected_fingerprint is not None and expected.fingerprint != expected_fingerprint:
                raise GraphContractError('GRAPH_SNAPSHOT_CHANGED_REPEAT_DRY_RUN')
            if not any(diff.values()):
                self.projection.verify_schema()
                return summary
            self.projection.ensure_schema()
            from neo4j import unit_of_work

            @unit_of_work(timeout=self.projection.driver.settings.neo4j_sync_timeout_seconds)
            def reconcile(tx):
                actual = self.projection.read(tx=tx)
                delta = graph_diff(expected, actual)
                self.projection.remove(delta['extra_nodes'], delta['extra_edges'], tx=tx,
                                       batch_size=self.projection.driver.settings.neo4j_batch_size)
                self.projection.merge(expected, tx=tx,
                    added_nodes=delta['missing_nodes'], added_edges=delta['missing_edges'],
                    batch_size=self.projection.driver.settings.neo4j_batch_size)
                after = self.projection.read(tx=tx)
                if after != expected:
                    raise GraphContractError('GRAPH_RECONCILIATION_DIFF')
                return sum(len(items) for items in delta.values())

            with self.projection.driver.session(write=True) as session:
                writes = session.execute_write(reconcile)
            after = self.projection.read()
            if after != expected:
                raise GraphContractError('GRAPH_READBACK_DIFF')
            return {**summary, 'status': 'MATCH', 'actual': after.counts(),
                    'diff': diff_counts(graph_diff(expected, after)), 'writes': writes}
