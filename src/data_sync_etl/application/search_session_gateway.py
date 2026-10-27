"""HTTP adapter for rank reuse. Requires an independently verified authority.

Production composition deliberately supplies no adapter until a revision fence
covers every writer (ACL, metadata, content, model, dictionary and provider).
Callbacks must rank complete bounded references and hydrate with current rights.
No response DTO, snippet, URL or authorization decision is retained here.
"""
from dataclasses import asdict
from threading import BoundedSemaphore

from data_sync_etl.application.search_sessions import SearchResultSessions
from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.search import SearchError


class SearchSessionGateway:
    def __init__(self, authority, rank_once, hydrate, envelope, **limits):
        self.sessions = SearchResultSessions(authority, **limits)
        self.rank_once, self.hydrate, self.envelope = rank_once, hydrate, envelope
        self._capacity = BoundedSemaphore(4)

    @staticmethod
    def keys(criteria, access):
        if not access.actor_id or not access.internal:
            raise SearchError('SEARCH_SESSION_UNAVAILABLE', 409)
        scope = asdict(access)
        scope['resource_ids'] = sorted(scope['resource_ids'])
        return digest(scope), digest(criteria)

    def read(self, criteria, access, *, page, size, token=None, window_pages=1):
        if not self._capacity.acquire(blocking=False):
            raise SearchError('SEARCH_SESSION_BUSY', 503)
        try:
            owner, key = self.keys(criteria, access)
            if token is None:
                token = self.sessions.begin(owner, key, lambda: self.rank_once(criteria, access))
            revision = self.sessions._revision(owner)
            header = self.envelope(criteria, access)
            if (header.get('message') != 'OK' or any(value not in ('OK', 'READY', 'DISABLED', 'NOT_REQUESTED')
                    for value in (header.get('branch_status') or {}).values())):
                raise SearchError('SEARCH_SESSION_UNAVAILABLE', 409)

            def read(number):
                result = self.sessions.page(token, owner, key, number, size,
                    lambda refs: self.hydrate(refs, criteria, access))
                return {**header, **result}

            result = read(page)
            window = [read(number) for number in range(page + 1,
                min(page + window_pages, result['total_pages'] + 1))]
            if self.sessions._revision(owner) != revision:
                raise SearchError('SEARCH_SESSION_MISS', 409)
            return {**result, 'search_session': {'id': token, 'ttl_seconds': self.sessions.ttl},
                    'page_window': window, 'total_exact': True}
        finally:
            self._capacity.release()
