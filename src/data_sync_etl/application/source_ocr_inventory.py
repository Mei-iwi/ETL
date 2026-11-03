"""Bounded metadata audit inside the existing SOURCE OCR worker, without file IO.

Inventory authority comes from a live application principal AND the registered
metadata source. It never grants process, raw-text access or external ownership.
"""
import json
from collections import Counter
from datetime import datetime, timedelta
from hashlib import sha256
from time import monotonic

from data_sync_etl.adapters.source.postgresql_ocr_read import PostgreSQLSourceOCRReader
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.resource_deletion import resource_deleted
from data_sync_etl.domain.search import SearchError

AUDIT_KEY = '_source_ocr_audit'


class SourceOCRInventory:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.uow = pipeline.uow
        self.stream = 'ocr-audit-' + sha256(pipeline.contract.namespace.encode()).hexdigest()[:16]
        self.next_scan = 0.0
        self.summary = {}
        self._priority_turn = 0

    def ready(self, *, limit=10):
        try:
            registration, resources, _ = self._scope()
        except Exception:
            return []
        with self.uow() as repo:
            return repo.source_ocr_ready_versions(registration.namespace, resource_ids=resources, limit=limit)

    def _scope(self):
        from data_sync_etl.security.master_service import MasterIdentityReader, eligible
        actor, _role = self.pipeline._scheduler_identity()
        registrations = [item for item in self.pipeline.resolver.source_registry.registrations()
                         if item.enabled and item.namespace == self.pipeline.contract.namespace
                         and item.backend.lower() == 'postgresql' and item.capabilities.read_metadata
                         and item.capabilities.exact_version_lookup]
        if len(registrations) != 1 or not actor or not actor.startswith('master:'):
            raise DomainError('SOURCE_OCR_INVENTORY_AUTHORIZATION_REQUIRED')
        user = MasterIdentityReader(self.pipeline.settings, engine=self.pipeline.container.engine).read(
            user_id=actor.removeprefix('master:'))
        if not eligible(user):
            raise DomainError('SOURCE_OCR_INVENTORY_AUTHORIZATION_REQUIRED')
        with self.uow() as repo:
            mapped = repo.get('master_users', actor.removeprefix('master:'))
        if not mapped or mapped.get('source_namespace') != registrations[0].namespace:
            raise DomainError('SOURCE_OCR_INVENTORY_AUTHORIZATION_REQUIRED')
        admin = self.pipeline.settings.admin_enabled and user['role_code'] == 'ADMIN'
        return registrations[0], None if admin else frozenset(user['resource_ids']), user

    def once(self, *, limit=10, force=False, prioritize_new=False):
        if not force and monotonic() < self.next_scan:
            return []
        self.next_scan = monotonic() + 5
        ready = []
        with self.uow.processing_lock(self.stream) as acquired:
            if not acquired:
                return ready
            try:
                registration, resources, principal = self._scope()
            except Exception:
                self.summary = {'status': 'WAITING_FOR_INVENTORY_AUTHORIZATION'}
                self.next_scan = monotonic() + 30
                return ready
            with self.uow() as repo:
                repo.ensure_checkpoint(self.stream)
                checkpoint = repo.get('sync_checkpoints', self.stream)
                after = checkpoint.get('last_id')
                priority = False
                versions = []
                if prioritize_new:
                    self._priority_turn += 1
                    # Every fourth turn still advances the complete inventory.
                    if self._priority_turn % 4:
                        versions = repo.source_ocr_inventory_page(registration.namespace,
                            resource_ids=resources, limit=limit, unaudited_only=True)
                        priority = bool(versions)
                if not versions:
                    versions = repo.source_ocr_inventory_page(registration.namespace, after=after,
                                                              resource_ids=resources, limit=limit)
            counts = Counter(discovered=len(versions), inspected=0, classified=0, deferred=0, errored=0)
            authority = dict(principal)
            if resources is None:
                authority.pop('resource_ids', None)  # Admin inventory is not an ownership grant.
            scope = self.pipeline.contract.namespace_scope
            scope_signature = (dict(actor=scope.service_actor_id, role=scope.service_role,
                                    lifecycles=sorted(scope.lifecycles), publications=sorted(scope.publications))
                               if scope else None)
            for version in versions:
                with self.uow.processing_lock('audit:' + version['id']) as locked:
                    if not locked:
                        counts['deferred'] += 1
                        continue
                    with self.uow() as repo:
                        current = repo.get('resource_versions', version['id'])
                        resource = repo.get('master_learning_resources', current['resource_id'])
                    previous = (current.get('processing_options') or {}).get(AUDIT_KEY, {})
                    resource_authority = {key: value for key, value in resource.items() if key in (
                        'id', 'source_origin', 'source_namespace', 'source_record_key', 'created_by',
                        'is_active', 'publication_status', 'deleted_at', 'master_deleted_at', 'source_deleted_at',
                        'source_missing_at', 'deletion_revision', 'visibility', 'review_status', 'license_restricted',
                        'acl_denied', 'access_denied', 'blocked', 'revoked', 'is_deleted', 'is_private')}
                    signature = sha256(json.dumps([current['source_hash'], current['lifecycle_status'],
                        (current.get('processing_options') or {}).get('_source_ocr'),
                        (current.get('processing_options') or {}).get('source_version_identity'),
                        resource_authority, registration.model_dump(), authority,
                        sorted(self.pipeline.contract.actor_ids), sorted(self.pipeline.contract.version_ids),
                        sorted(self.pipeline.contract.roles), sorted(self.pipeline.contract.lifecycles),
                        self.pipeline.contract.allow_etl_created_versions,
                        scope_signature], sort_keys=True, default=str).encode()).hexdigest()
                    if (not force and previous.get('signature') == signature and previous.get('next_check_at')
                            and datetime.fromisoformat(previous['next_check_at']) > now()):
                        if previous.get('execution_authorized') and previous.get('needs_execution'):
                            ready.append(version['id'])
                        counts['deferred'] += 1
                        continue
                    counts['inspected'] += 1
                    with self.uow() as repo:
                        latest = repo.get('resource_versions', version['id'], lock=True)
                        options = dict(latest.get('processing_options') or {})
                        options[AUDIT_KEY] = {**previous, 'status': 'CHECKING', 'needs_execution': False}
                        repo.update('resource_versions', version['id'], {'processing_options': options})
                    try:
                        result = self.inspect(current, resource, registration, resources)
                    except Exception as error:
                        code = str(error) if isinstance(error, DomainError) else 'SOURCE_OCR_INVENTORY_UNAVAILABLE'
                        if len(code) > 80 or not code.replace('_', '').isalnum():
                            code = 'SOURCE_OCR_INVENTORY_UNAVAILABLE'
                        invalid = code in ('SOURCE_VERSION_MUTATED', 'SOURCE_VERSION_MISSING',
                            'SOURCE_OCR_PAGE_CONFLICT', 'SOURCE_OCR_UNIT_CONFLICT', 'SOURCE_OCR_STALE',
                            'SOURCE_OCR_INVALID_STATE', 'SOURCE_OCR_INVALID_IDENTITY', 'SOURCE_OCR_JOB_CONFLICT',
                            'SOURCE_OCR_UNAUTHORIZED', 'SOURCE_OCR_INVENTORY_AUTHORIZATION_REQUIRED',
                            'SOURCE_OCR_PROCESSING_IDENTITY_CHANGED', 'SOURCE_OCR_NAMESPACE_MISMATCH')
                        result = {'status': 'INVALID_OR_REVOKED' if invalid else 'RETRYABLE_ERROR', 'reason': code}
                        counts['errored'] += 1
                    attempts = min(20, previous.get('attempts', 0) + 1) if result['status'] == 'RETRYABLE_ERROR' else 0
                    delay = min(3600, 30 * 2 ** min(attempts, 6)) if attempts else (
                        300 if result['status'] in ('WAITING_FOR_PROCESS_AUTHORIZATION', 'UNVERIFIED') else 60)
                    record = dict(result, signature=signature, checked_at=now().isoformat(), attempts=attempts,
                                  next_check_at=(now() + timedelta(seconds=delay)).isoformat())
                    with self.uow() as repo:
                        latest = repo.get('resource_versions', version['id'], lock=True)
                        options = dict(latest.get('processing_options') or {})
                        # Metadata may commit while a remote snapshot is in flight.
                        if latest['source_hash'] != current['source_hash']:
                            counts['deferred'] += 1
                            continue
                        options[AUDIT_KEY] = record
                        repo.update('resource_versions', version['id'], {'processing_options': options})
                    counts['classified'] += 1
                    counts[result['status']] += 1
                    if result.get('execution_authorized') and result.get('needs_execution'):
                        ready.append(version['id'])
            # Cursor advances only after durable classifications; errors get their
            # own bounded retry timestamp. Wrapping revisits old and late commits.
            if not priority:
                with self.uow() as repo:
                    repo.update('sync_checkpoints', self.stream, {'last_id': versions[-1]['id'] if versions else None,
                                'last_updated_at': now(), 'updated_at': now()})
            self.summary = dict(counts, cursor=versions[-1]['id'] if versions else None,
                                namespace=registration.namespace, checked_at=now().isoformat())
            if not versions:
                self.next_scan = monotonic() + 5
        return ready

    def dispatched(self, version_id):
        with self.uow() as repo:
            version = repo.get('resource_versions', version_id, lock=True)
            options = dict(version.get('processing_options') or {})
            audit = dict(options.get(AUDIT_KEY) or {})
            state = options.get('_source_ocr') or {}
            audit.update(status='QUEUED', needs_execution=False, execution_requested_at=now().isoformat(),
                         source_job_id=state.get('source_job_id'))
            options[AUDIT_KEY] = audit
            repo.update('resource_versions', version_id, {'processing_options': options})

    def defer(self, version_id):
        # A revoked/stale ready hint must not occupy every slot of the ready
        # query forever. Re-audit it; never interpret the old hint as a grant.
        with self.uow() as repo:
            version = repo.get('resource_versions', version_id, lock=True)
            options = dict(version.get('processing_options') or {})
            options[AUDIT_KEY] = {**options.get(AUDIT_KEY, {}), 'status':'RECHECK_REQUIRED',
                                 'needs_execution':False, 'execution_authorized':False,
                                 'next_check_at':now().isoformat()}
            repo.update('resource_versions', version_id, {'processing_options':options})

    def inspect(self, version, resource, registration, resources):
        p = self.pipeline
        if resource_deleted(resource) or not resource.get('is_active') or resource.get('source_missing_at'):
            return {'status': 'INVALID_OR_REVOKED', 'reason': 'SOURCE_RESOURCE_REVOKED'}
        with self.uow() as repo:
            imports = repo.find('source_file_imports', {'target_version_id': version['id']}, limit=2)
        identity = (version.get('processing_options') or {}).get('source_version_identity') or {}
        if len(imports) == 1:
            source_id = imports[0]['source_version_id']
        elif not imports and identity.get('namespace') == registration.namespace and identity.get('source_id') == registration.source_id:
            source_id = identity.get('source_version_id')
        else:
            return {'status': 'WAITING_FOR_SOURCE_MAPPING', 'reason': 'BLOCKED_SOURCE_MAPPING'}
        adapter = p.resolver.source_registry.adapter_for_source_id(registration.source_id, 'exact_version_lookup')
        bulk = getattr(adapter, 'get_versions', None)
        source = bulk([source_id]).get(source_id) if callable(bulk) else adapter.get_version(source_id)
        if (not source or source.get('resource_id') != resource.get('source_record_key')
                or source.get('source_version_number') != version['version_number']
                or source.get('source_hash', '').lower() != version['source_hash'].lower()
                or not source.get('resource_active', True) or source.get('resource_deleted_at') is not None):
            return {'status': 'INVALID_OR_REVOKED', 'reason': 'SOURCE_VERSION_MUTATED'}

        def may_inspect(candidate):
            reg, allowed, _user = self._scope()
            with self.uow() as repo:
                live = repo.get('master_learning_resources', resource['id'])
            return bool(live and reg.namespace == candidate['namespace'] == live.get('source_namespace')
                        and candidate['master_version_id'] == version['id']
                        and (allowed is None or live['id'] in allowed))

        reader = PostgreSQLSourceOCRReader(p.source_engine, authorize=may_inspect,
            lifecycle_allowed=lambda _value: True, statement_timeout_seconds=p.contract.statement_timeout_seconds)
        snapshot = reader.snapshot(namespace=registration.namespace, source_version_id=source_id,
                                   master_version_id=version['id'], source_hash=version['source_hash'])
        state = p.state(version['id'])
        job = snapshot['job']
        if job and (not state or state.get('source_job_id') != job['id']):
            return {'status': 'UNVERIFIED', 'observed_status': 'UNVERIFIED', 'external_status': 'UNTRUSTED',
                    'reason': 'OWNER_PROVENANCE_CONTRACT_REQUIRED', 'source_job_id': job['id'],
                    'total_pages': job['total_pages'], 'completed_pages': job['completed_pages']}
        actor, role = p._scheduler_identity()
        authorized = False
        authorization_reason = 'SOURCE_PROCESS_AUTHORIZATION_REQUIRED'
        try:
            if len(imports) != 1:
                authorization_reason = 'SOURCE_OCR_EXECUTION_MAPPING_REQUIRED'
            elif source.get('source_lifecycle_status') not in p.contract.lifecycles:
                authorization_reason = 'SOURCE_OCR_LIFECYCLE_NOT_AUTHORIZED'
            elif not p.created_version_allowed(version['id'], actor):
                authorization_reason = 'SOURCE_OCR_VERSION_NOT_AUTHORIZED'
            elif actor not in p.contract.actor_ids or role not in p.contract.roles:
                authorization_reason = 'SOURCE_OCR_ACTOR_CONTRACT_DENIED'
            else:
                access = p._access(actor, role, resource['id'])
                p.container.search.authorize_source_processing_version(version['id'], access=access)
                authorized = True
        except SearchError:
            pass  # A current application resource grant was denied.
        except DomainError as error:
            if str(error) not in ('SOURCE_OCR_ACTOR_GRANT_REQUIRED', 'SOURCE_CREATE_ACTOR_MAPPING_REQUIRED'):
                raise
        if authorized and not state:
            with self.uow() as repo:
                legacy = repo.find('ocr_jobs', {'resource_version_id': version['id']}, limit=1)
            if legacy:
                return {'status': 'UNVERIFIED', 'observed_status': 'ABSENT',
                        'reason': 'SOURCE_OCR_LEGACY_PROVENANCE_REQUIRED'}
        observed = 'ABSENT'
        if state and job:
            # A process grant is not minted by metadata discovery. Existing
            # proof hydration still passes the original live raw authority.
            if not p.authorize_identity(state['identity']):
                authorized = False
                observed = 'UNVERIFIED'
            else:
                result = p.probe.probe(snapshot, generation=state['generation'], processing_fingerprint=state['fingerprint'])
                observed = {'UNVERIFIABLE': 'UNVERIFIED', 'NOT_READY': 'PARTIAL'}.get(result['status'], result['status'])
        elif p.mongo.has_ocr_pages(source_id):
            observed = 'UNVERIFIED'
        status = observed if authorized else 'WAITING_FOR_PROCESS_AUTHORIZATION'
        if observed == 'UNVERIFIED':
            status = 'UNVERIFIED'
        exhausted = bool(imports and (imports[0].get('attempts', 0) >= p.settings.source_file_import_max_attempts
                         or imports[0].get('status') in ('FAILED', 'RETRY_EXHAUSTED', 'CANCELLED')))
        if exhausted and authorized and observed in ('ABSENT', 'PARTIAL'):
            status = 'RETRY_EXHAUSTED'
        return {'status': status, 'observed_status': observed, 'execution_authorized': bool(authorized),
                'needs_execution': bool(authorized and observed in ('ABSENT', 'PARTIAL')
                    and not exhausted
                    and not (state and state.get('scheduled') and job and job['status'] in ('PENDING', 'RUNNING'))),
                'reason': ('SOURCE_PROCESS_RETRY_EXHAUSTED' if exhausted and authorized else
                           None if authorized else authorization_reason),
                'source_job_id': job['id'] if job else None}
