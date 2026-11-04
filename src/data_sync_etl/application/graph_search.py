"""Optional bounded advice. Graph never grants access or changes ranking."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import BoundedSemaphore, Lock


class GraphSearchAdvisor:
    def __init__(self, graph, authority, settings):
        self.graph, self.authority, self.settings = graph, authority, settings
        self.metrics = Counter()
        self._lock, self._slots = Lock(), BoundedSemaphore(2)
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='graph-advice')

    def _count(self, key):
        with self._lock:
            self.metrics[key] += 1

    @staticmethod
    def _canonical(rows, requested):
        result = {}
        fields = {'content_unit_id', 'resource_version_id', 'resource_id', 'subject_ids', 'grade_ids'}
        for row in rows:
            key = row.get('content_unit_id')
            if set(row) != fields or key not in requested or key in result:
                raise ValueError('GRAPH_MAPPING_DIFF')
            result[key] = {**row, 'subject_ids': sorted(set(row['subject_ids'])),
                          'grade_ids': sorted(set(row['grade_ids']))}
        if set(result) != set(requested):
            raise ValueError('GRAPH_MAPPING_DIFF')
        return result

    def _lookup(self, expected):
        ids = list(expected)
        actual = self._canonical(self.graph.resolve(ids), ids)
        fresh = self._canonical(self.authority.resolve(ids), ids)
        if actual != fresh:
            raise ValueError('GRAPH_MAPPING_DIFF')
        for key, item in expected.items():
            if (actual[key]['resource_id'] != item['resource_id']
                    or actual[key]['resource_version_id'] != item['resource_version_id']):
                raise ValueError('GRAPH_MAPPING_DIFF')
        return actual

    def inspect(self, items, *, remaining=None):
        if not self.settings.neo4j_search_enabled or not items:
            return {}
        expected = {item['bag_id']: {'resource_id': item['resource_id'],
                     'resource_version_id': item['resource_version_id']} for item in items}
        if len(expected) > 100:
            self._count('candidate_limit_fallback')
            return {}
        # Reserve at least 75% of the remaining HTTP budget for the existing
        # final Mongo/PostgreSQL/SOURCE authorization phase. No timeout increase.
        budget = self.settings.neo4j_query_timeout_seconds
        if remaining is not None:
            budget = min(budget, remaining / 4)
        if budget <= 0 or not self._slots.acquire(blocking=False):
            self._count('busy_or_budget_fallback')
            return {}
        future = self._pool.submit(self._lookup, expected)
        future.add_done_callback(lambda _: self._slots.release())
        try:
            result = future.result(timeout=budget)
        except TimeoutError:
            future.cancel()
            self._count('timeout_fallback')
            return {}
        except Exception:
            self._count('unavailable_or_stale_fallback')
            return {}
        self._count('parity')
        if self.settings.neo4j_search_mode == 'active':
            self._count('active_resolution')
            return result
        self._count('shadow')
        return {}

    def close(self):
        self._pool.shutdown(wait=True, cancel_futures=True)
