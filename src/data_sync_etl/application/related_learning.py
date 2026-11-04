"""Optional relationship suggestions; never part of Search ranking or authority."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import BoundedSemaphore
from urllib.parse import quote

from data_sync_etl.logging import event


class RelatedLearning:
    def __init__(self, graph, authority, settings):
        self.graph, self.authority, self.settings = graph, authority, settings
        self._slots = BoundedSemaphore(2)
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='related-learning')

    def run(self, resource_id, access):
        if (not self.settings.neo4j_enabled or not self.settings.neo4j_search_enabled
                or self.settings.neo4j_search_mode != 'active' or not access.internal):
            return []
        if not self._slots.acquire(blocking=False):
            return []
        future = self._pool.submit(self._lookup, resource_id, access)
        future.add_done_callback(lambda _: self._slots.release())
        try:
            return future.result(timeout=min(3, self.settings.neo4j_query_timeout_seconds))
        except TimeoutError:
            future.cancel()
            event(stage='graph_related', action='TIMEOUT_FALLBACK')
        except Exception:
            event(stage='graph_related', action='UNAVAILABLE_OR_STALE_FALLBACK')
        return []

    def _lookup(self, resource_id, access):
        before = self.authority.read(resource_id, access)
        if resource_id not in before or not 1 <= len(before) <= 100:
            return []
        rows = self.graph.related(resource_id, sorted(before))
        if len(rows) > 99:
            return []
        seen, result = set(), []
        seed = before[resource_id]
        for row in rows:
            if set(row) != {'resource_id', 'subject_ids', 'grade_ids'}:
                return []
            key = row['resource_id']
            if key == resource_id or key not in before or key in seen:
                return []
            seen.add(key)
            other = before[key]
            subjects = sorted(set(seed['subject_ids']) & set(other['subject_ids']))
            grades = sorted(set(seed['grade_ids']) & set(other['grade_ids']))
            if (row['subject_ids'] != subjects or row['grade_ids'] != grades
                    or not (subjects or grades)):
                return []
            reason = ('Cùng môn học và khối lớp' if subjects and grades else
                      'Cùng môn học' if subjects else 'Cùng khối lớp')
            result.append((-(bool(subjects) + bool(grades)), key, {
                'resource_id': key, 'title': other['title'], 'reason_label': reason,
                'url': '/library/' + quote(key, safe=''),
            }))
        # Fresh SOURCE/MASTER authority, including current version/hash, wins over
        # graph and over the first snapshot. No cross-request/user cache exists.
        after = self.authority.read(resource_id, access)
        if after != before:
            return []
        return [item for _, _, item in sorted(result, key=lambda row: (row[0], row[1]))[:3]]

    def close(self):
        self._pool.shutdown(wait=True, cancel_futures=True)
