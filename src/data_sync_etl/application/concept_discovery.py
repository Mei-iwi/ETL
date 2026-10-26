"""Standalone offline discovery/review orchestration; never called by HTTP."""
import json
import os
import re
from time import monotonic, time

from data_sync_etl.domain.concepts import Concept, default_dictionary
from data_sync_etl.domain.corpus_concepts import (
    DEFAULT_LIMITS,
    POLICY,
    EligibilityFence,
    discover,
    eligible,
    fingerprint,
    valid_relation,
)
from data_sync_etl.domain.indexing import digest
from data_sync_etl.ports.concept_corpus import ConceptBlocked


def term_target(term, candidate):
    return digest([term['language'], term['text'], candidate['domain'], candidate['sense']])


class ReviewLedger:
    """Append-only hash chain, bounded private audit file, separate term/relation review.

    Reviewer IDs are operator-supplied identities, not proof of independent review.
    All current publication is explicitly labelled offline_fixture.
    """
    def __init__(self, store, max_bytes=8 * 1024 * 1024, max_records=10000, on_decision=lambda _: None):
        self.store, self.max_bytes, self.max_records = store, max_bytes, max_records
        self.on_decision, self._count = on_decision, 0

    def snapshot(self):
        target = self.store.path('review/decisions.jsonl')
        previous, decisions = '0' * 64, {}
        self._count = 0
        if target.exists():
            if target.stat().st_size > self.max_bytes:
                raise ConceptBlocked('REVIEW_LEDGER_BUDGET')
            with target.open(encoding='utf-8') as source:
                for index, line in enumerate(source):
                    if index >= self.max_records or len(line) > 4096:
                        raise ConceptBlocked('REVIEW_LEDGER_BUDGET')
                    item = json.loads(line)
                    actual = item.pop('hash')
                    if item.get('previous') != previous or digest(item) != actual:
                        raise ConceptBlocked('REVIEW_CHAIN_INVALID')
                    previous = actual
                    self._count = index + 1
                    decisions[(item['kind'], item['target'], item['binding'])] = item['decision']
        return previous, decisions

    def decide(self, candidate, *, kind, target, decision, reviewer):
        if (not re.fullmatch(r'[A-Za-z0-9:_-]{3,80}', reviewer)
                or reviewer.upper().startswith(('YOUR_', 'ACTUAL_', 'PLACEHOLDER'))):
            raise ValueError('Explicit reviewer identity required; no email/placeholder')
        if decision not in ('approved', 'rejected') or kind not in ('term', 'relation'):
            raise ValueError('Invalid review decision')
        targets = ({term_target(term, candidate) for term in candidate['terms']} if kind == 'term' else {candidate['id']})
        if target not in targets:
            raise ValueError('Review target mismatch')
        with self.store.writer_lock():
            previous, _ = self.snapshot()
            if self._count >= self.max_records:
                raise ConceptBlocked('REVIEW_LEDGER_BUDGET')
            record = {'previous': previous, 'kind': kind, 'target': target, 'decision': decision,
                'binding': candidate['binding'], 'reviewer': reviewer, 'reviewed_at': time(),
                'policy': POLICY, 'scope': 'offline_fixture'}
            record['hash'] = digest(record)
            target_path = self.store.path('review/decisions.jsonl')
            target_path.parent.mkdir(exist_ok=True, mode=0o700)
            data = (json.dumps(record, sort_keys=True, ensure_ascii=False) + '\n').encode('utf-8')
            if (target_path.stat().st_size if target_path.exists() else 0) + len(data) > self.max_bytes:
                raise ConceptBlocked('REVIEW_LEDGER_BUDGET')
            with target_path.open('ab') as output:
                os.chmod(target_path, 0o600)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            self.on_decision(record['hash'])
            return record['hash']


def reviewed_concept(candidate, ledger_hash, decisions):
    if candidate['kind'] not in ('translation', 'synonym', 'abbreviation'):
        return None
    required = [('relation', candidate['id']), *[('term', term_target(term, candidate)) for term in candidate['terms']]]
    if any(decisions.get((kind, target, candidate['binding'])) != 'approved' for kind, target in required):
        return None
    source = [{'name': 'offline corpus review; not independent quality acceptance',
               'identifier': digest([candidate['binding'], ledger_hash])}]
    concept_id = candidate.get('concept_id', candidate['id'])
    labels, terms = {}, []
    for term in candidate['terms']:
        lang, text = term['language'], term['text']
        preferred = lang not in labels
        if preferred:
            labels[lang] = text
        terms.append(dict(text=text, normalized=text, language=lang, concept_id=concept_id,
            domain=candidate['domain'], kind='preferred' if preferred else 'synonym',
            confidence=0.9, review_status='approved', sources=source))
    # Approved abbreviation still needs the existing Dictionary domain/context gate.
    if candidate['kind'] == 'abbreviation' and len(terms) > 1:
        terms[1]['kind'] = 'abbreviation'
        labels.pop(terms[1]['language'], None)
        labels[terms[0]['language']] = terms[0]['text']
    return Concept.model_validate(dict(concept_id=concept_id, preferred_labels=labels,
        definition='Explicit corpus mapping reviewed in an offline fixture ledger.', domain=candidate['domain'],
        terms=terms, sources=source, review_status='approved', version=POLICY))


class ConceptDiscoveryWorker:
    def __init__(self, inventory, store, *, reader=None, limits=DEFAULT_LIMITS, batch_size=20,
                 tick_seconds=2., retry_limit=5, clock=time, pressure=lambda: False):
        if not 1 <= batch_size <= 500 or not 0 < tick_seconds <= 300 or not 1 <= retry_limit <= 20:
            raise ValueError('Invalid worker budgets')
        self.inventory, self.store, self.reader, self.limits = inventory, store, reader, limits
        self.batch_size, self.tick_seconds, self.retry_limit = batch_size, tick_seconds, retry_limit
        self.clock, self.pressure = clock, pressure
        callback = getattr(inventory, 'set_review_token', lambda _: None)
        self.fence, self.ledger = EligibilityFence(inventory), ReviewLedger(store, on_decision=callback)
        callback(self.ledger.snapshot()[0])

    @staticmethod
    def contribution_key(resource_id):
        key = digest(resource_id)
        return f'contributions/{key[:2]}/{key}.json'

    def status(self):
        return self.store.read_json('checkpoints/state.json', default={
            'after': '', 'processed': 0, 'writes': 0, 'sweeps': 0, 'failures': 0,
            'paused': False, 'next_due': 0., 'status': 'NEW'})

    def tick(self):
        with self.store.writer_lock():
            state = self.status()
            if state['paused'] or self.pressure() or self.clock() < state['next_due']:
                return {**state, 'status': 'PAUSED_OR_WAITING'}
            started = monotonic()
            try:
                rows = self.inventory.page(state['after'], self.batch_size)
                # Revoke/delete first WITHIN the batch. Cursor commits only after
                # the entire original keyset batch is durable, never by priority ID.
                ordered = sorted(rows, key=lambda row: (eligible(row), row.resource_id))
                for resource in ordered:
                    key = self.contribution_key(resource.resource_id)
                    previous = self.store.read_json(key)
                    record = discover(resource, self.reader, self.limits)
                    current = self.inventory.current(resource.resource_id)
                    if current is None or fingerprint(current) != record['fingerprint']:
                        raise ConceptBlocked('DISCOVERY_SOURCE_CHANGED')
                    if (not previous or previous['fingerprint'] != record['fingerprint']
                            or previous.get('evidence_fingerprint') != record['evidence_fingerprint']
                            or previous.get('extractor') != record['extractor'] or previous.get('policy') != record['policy']):
                        self.store.write_json(key, record)
                        state['writes'] += 1
                    state['processed'] += 1
                    if resource.source_updated_at:
                        samples = [*state.get('lag_samples_seconds', []), max(0., self.clock() - resource.source_updated_at)][-64:]
                        ordered_lag = sorted(samples)
                        state.update(lag_samples_seconds=samples, detected_to_processed_seconds=monotonic() - started,
                            source_to_detected_p50_seconds=ordered_lag[len(ordered_lag) // 2],
                            source_to_detected_p95_seconds=ordered_lag[int(.95 * (len(ordered_lag) - 1))])
                    if monotonic() - started > self.tick_seconds:
                        # Replay already durable outputs next tick; do NOT lose the
                        # unprocessed remainder by advancing to the max resource ID.
                        raise ConceptBlocked('TICK_BUDGET_REPLAY')
                state.update(after=rows[-1].resource_id if rows else '', failures=0, error_code=None, status='CANDIDATE_ONLY',
                             last_processed_at=self.clock(), last_batch_seconds=monotonic() - started)
                if not rows:
                    state['sweeps'] += 1
                    state['last_completed_sweep_at'] = self.clock()
                state['next_due'] = self.clock() + 1.
            except Exception as error:
                state['failures'] += 1
                code = str(error) if isinstance(error, ConceptBlocked) else 'DISCOVERY_FAILED'
                code = code if re.fullmatch(r'[A-Z_]{2,80}', code) else 'CORPUS_BLOCKED'
                state.update(status='RETRY_REQUIRED', error_code=code,
                    next_due=self.clock() + min(300, 2 ** state['failures']), paused=state['failures'] >= self.retry_limit)
                if state['paused']:
                    self.store.write_json('quarantine/worker.json', {'error_code': state['error_code'],
                        'cursor_hash': digest(state['after']), 'failures': state['failures'], 'raw_text': 'OMITTED'})
            self.store.write_json('checkpoints/state.json', state)
            return state

    def reconcile(self):
        with self.store.writer_lock():
            state = self.status()
            state.update(after='', failures=0, paused=False, next_due=0.)
            self.store.write_json('checkpoints/state.json', state)

    def contributions(self):
        # Bounded filesystem iterator. Never materialize the inventory in RAM.
        root = self.store.path('contributions')
        if root.exists():
            with os.scandir(root) as partitions:
                for partition in partitions:
                    if partition.is_symlink() or not partition.is_dir():
                        raise ConceptBlocked('ARTIFACT_LINK_FORBIDDEN')
                    self.store.path('contributions/' + partition.name)
                    with os.scandir(partition.path) as files:
                        for item in files:
                            if item.name.endswith('.json'):
                                yield self.store.read_json(f'contributions/{partition.name}/{item.name}')

    def build(self):
        if self.inventory.scope != 'offline_fixture':
            raise ConceptBlocked('LIVE_PUBLISH_NOT_VERIFIED')
        token = self.fence.capture()
        ledger_hash, decisions = self.ledger.snapshot()
        coverage = {'resources_scanned': 0, 'resources_eligible': 0, 'relations_seen': 0,
                    'approved_relations': 0, 'pending_or_rejected': 0}
        def concepts():
            for record in self.contributions():
                coverage['resources_scanned'] += 1
                current = self.inventory.current(record['resource_id'])
                if not eligible(current) or fingerprint(current) != record['fingerprint']:
                    continue
                coverage['resources_eligible'] += 1
                for candidate in record['relations']:
                    coverage['relations_seen'] += 1
                    if not valid_relation(candidate, record['fingerprint']):
                        raise ConceptBlocked('CANDIDATE_BINDING_INVALID')
                    concept = reviewed_concept(candidate, ledger_hash, decisions)
                    if concept is not None:
                        coverage['approved_relations'] += 1
                        yield concept
                    else:
                        coverage['pending_or_rejected'] += 1
        return self.store.build(concepts(), seed_hash=default_dictionary().fingerprint,
            source_token=token, ledger_hash=ledger_hash, expected_parent=self.store.current(),
            fence=lambda: self.fence.unchanged(token) and self.ledger.snapshot()[0] == ledger_hash, coverage=coverage)
