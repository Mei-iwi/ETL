"""Bounded durable coalesced outbox, reused derived artifact state, not a live feed."""
from data_sync_etl.domain.corpus_concepts import fingerprint
from data_sync_etl.domain.indexing import digest
from data_sync_etl.ports.concept_corpus import ConceptBlocked

PRIORITY = {'revoke': 0, 'review': 1, 'update': 2, 'add': 3}


class PendingConceptChanges:
    def __init__(self, store, *, max_pending=1000):
        if not 1 <= max_pending <= 1000:
            raise ValueError('Bounded change queue required')
        self.store, self.max_pending = store, max_pending

    def _read(self):
        state = self.store.read_json('events/pending.json', default={})
        if not isinstance(state, dict) or len(state) > self.max_pending:
            raise ConceptBlocked('CHANGE_QUEUE_BUDGET')
        return state

    def quarantine_invalid_locked(self):
        """Caller owns writer fence. Persist a redacted receipt before removal."""
        state, removed = self._read(), 0
        for key, item in list(state.items()):
            valid = (isinstance(item, dict) and set(item) == {'resource_id', 'revision', 'fingerprint', 'kind'}
                and isinstance(item.get('resource_id'), str) and 0 < len(item['resource_id']) <= 128
                and key == digest(item['resource_id']) and type(item.get('revision')) is int and item['revision'] >= 0
                and isinstance(item.get('fingerprint'), str) and len(item['fingerprint']) == 64
                and all(c in '0123456789abcdef' for c in item['fingerprint'])
                and isinstance(item.get('kind'), str) and item['kind'] in PRIORITY)
            if not valid:
                receipt = digest([key, item])
                self.store.write_json('quarantine/events/' + receipt + '.json',
                    {'event_hash': receipt, 'reason': 'INVALID_CHANGE_EVENT', 'raw_event': 'OMITTED'})
                del state[key]
                removed += 1
        if removed:
            self.store.write_json('events/pending.json', state)
        return removed

    def enqueue(self, resource, kind):
        return self.enqueue_many([(resource, kind)])

    def enqueue_many(self, changes):
        with self.store.writer_lock():
            state = self._read()
            changed = False
            for index, (resource, kind) in enumerate(changes):
                if index >= self.max_pending:
                    raise ConceptBlocked('CHANGE_QUEUE_BUDGET')
                if kind not in PRIORITY:
                    raise ValueError('Unknown change kind')
                key = digest(resource.resource_id)
                item = {'resource_id': resource.resource_id, 'revision': resource.revision,
                    'fingerprint': fingerprint(resource), 'kind': kind}
                old = state.get(key)
                if old and old['revision'] > item['revision']:
                    continue
                if old and old['revision'] == item['revision'] and old['fingerprint'] != item['fingerprint']:
                    raise ConceptBlocked('CONFLICTING_CHANGE_REVISION')
                if old and old['fingerprint'] == item['fingerprint']:
                    item['kind'] = min((kind, old['kind']), key=PRIORITY.get)
                if key not in state and len(state) >= self.max_pending:
                    raise ConceptBlocked('CHANGE_QUEUE_BUDGET')
                state[key] = item
                changed = True
            if not changed:
                return False
            self.store.write_json('events/pending.json', state)
        return True

    def peek(self, limit=20):
        if not 1 <= limit <= 100:
            raise ValueError('Bounded event batch required')
        return sorted(self._read().values(), key=lambda item: (PRIORITY[item['kind']], item['resource_id']))[:limit]

    def acknowledge_locked(self, event):
        """Caller holds writer lock and has made contribution durable first."""
        self.acknowledge_many_locked([event])

    def acknowledge_many_locked(self, events):
        state = self._read()
        changed = False
        for index, event in enumerate(events):
            if index >= 100:
                raise ConceptBlocked('CHANGE_QUEUE_BUDGET')
            key = digest(event['resource_id'])
            if state.get(key) == event:
                del state[key]
                changed = True
        if changed:
            self.store.write_json('events/pending.json', state)
