"""Optional relationship suggestions; never part of Search ranking or authority."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextvars import copy_context
from threading import BoundedSemaphore
from urllib.parse import quote

from data_sync_etl.logging import event


class RelatedSeedDenied(Exception):
    pass


class RelatedUnavailable(Exception):
    pass


class RelatedLearning:
    def __init__(self, graph, authority, settings):
        self.graph, self.authority, self.settings = graph, authority, settings
        self._slots = BoundedSemaphore(2)
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='related-learning')

    def run(self, resource_id, access):
        return self._execute(resource_id, access)['items']

    @property
    def effective_mode(self):
        explicit = getattr(self.settings, 'neo4j_related_mode', None)
        if not self.settings.neo4j_enabled or (explicit is None and not self.settings.neo4j_search_enabled):
            return 'disabled'
        return explicit or self.settings.neo4j_search_mode

    def response(self, resource_id, access):
        return self._execute(resource_id, access, strict=True)

    def _execute(self, resource_id, access, *, strict=False):
        if self.effective_mode != 'active' or not access.internal:
            return {'items': [], 'status': 'disabled'}
        if not self._slots.acquire(blocking=False):
            return {'items': [], 'status': 'unavailable'}
        try:
            future = self._pool.submit(copy_context().run, self._lookup, resource_id, access)
        except RuntimeError:
            self._slots.release()
            return {'items': [], 'status': 'unavailable'}
        future.add_done_callback(lambda _: self._slots.release())
        try:
            budget = (getattr(self.settings, 'neo4j_related_timeout_seconds',
                              min(3, self.settings.neo4j_query_timeout_seconds))
                      if getattr(self.settings, 'neo4j_related_mode', None) is not None
                      else min(3, self.settings.neo4j_query_timeout_seconds))
            return {'items': future.result(timeout=budget), 'status': 'ready'}
        except RelatedSeedDenied:
            if strict:
                raise
            return {'items': [], 'status': 'unavailable'}
        except TimeoutError:
            future.cancel()
            event(stage='graph_related', action='TIMEOUT_FALLBACK')
        except Exception as exc:
            if strict and getattr(exc, 'status', None) in (401, 403):
                raise
            event(stage='graph_related', action='UNAVAILABLE_OR_STALE_FALLBACK')
        return {'items': [], 'status': 'unavailable'}

    def _lookup(self, resource_id, access):
        before = self.authority.read(resource_id, access)
        if resource_id not in before:
            raise RelatedSeedDenied()
        if not 1 <= len(before) <= 100:
            raise RelatedUnavailable()
        rows = self.graph.related(resource_id, sorted(before))
        if len(rows) > 99:
            raise RelatedUnavailable()
        seen, result = set(), []
        seed = before[resource_id]
        for row in rows:
            if set(row) != {'resource_id', 'subject_ids', 'grade_ids'}:
                raise RelatedUnavailable()
            key = row['resource_id']
            if key == resource_id or key not in before or key in seen:
                raise RelatedUnavailable()
            seen.add(key)
            other = before[key]
            subjects = sorted(set(seed['subject_ids']) & set(other['subject_ids']))
            grades = sorted(set(seed['grade_ids']) & set(other['grade_ids']))
            if (row['subject_ids'] != subjects or row['grade_ids'] != grades
                    or not (subjects or grades)):
                raise RelatedUnavailable()
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
            raise RelatedUnavailable()
        event(stage='graph_related', action='AUTHORIZED_TRAVERSAL',
              coverage={'graph_candidates': len(rows), 'authorized_items': min(3, len(result))})
        return [item for _, _, item in sorted(result, key=lambda row: (row[0], row[1]))[:3]]

    def close(self):
        self._pool.shutdown(wait=True, cancel_futures=True)
