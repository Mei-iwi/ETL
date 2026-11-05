"""SOURCE-owned OCR wired to MASTER 0020 metadata and the five Mongo collections."""
import json
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path, PurePosixPath

from pymongo.errors import PyMongoError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from data_sync_etl.adapters.mongo.source_ocr_read import MongoExistingSourceOCRReader
from data_sync_etl.adapters.persistence.legacy_ocr_text import LegacyOCRText
from data_sync_etl.adapters.source.postgresql_ocr_queue import (
    JOB_FIELDS,
    TASK_FIELDS,
    PostgreSQLSourceOCRQueue,
    source_sql_error,
)
from data_sync_etl.adapters.source.postgresql_ocr_read import PostgreSQLSourceOCRReader
from data_sync_etl.adapters.source.postgresql_ocr_write import PostgreSQLSourceOCRWriter
from data_sync_etl.application.bilingual import paired_result
from data_sync_etl.application.source_ocr_probe import SourceOCRProbe
from data_sync_etl.application.source_resource_create import SourceResourceCreateService
from data_sync_etl.db.session import make_engine
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.resource_deletion import resource_deleted
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.domain.source_ocr_contract import SourceOCRContract
from data_sync_etl.domain.source_ocr_ids import source_owned_id as source_owned_id
from data_sync_etl.domain.source_ocr_ids import stable_id as stable_id

STATE_KEY = '_source_ocr'
_PROCESSING_CALLER = ContextVar('source_ocr_processing_caller', default=None)


def load_source_ocr_contract(settings):
    path = settings.source_ocr_contract_file
    if path is None or not Path(path).is_file():
        raise DomainError('SOURCE_OCR_CONTRACT_REQUIRED')
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise DomainError('SOURCE_OCR_CONTRACT_INVALID') from None
    contract = SourceOCRContract.from_owner_contract(value, expected_namespace=settings.source_namespace)
    columns = dict(contract.columns)
    required = {'ocr_jobs': JOB_FIELDS | {'triggered_by', 'created_at'},
                'ocr_page_tasks': TASK_FIELDS,
                'content_units': {'id', 'mongo_id', 'resource_version_id', 'parent_unit_id', 'unit_type', 'title',
                                  'sequence_no', 'page_from', 'page_to', 'review_status', 'retrieval_eligible',
                                  'created_at', 'updated_at'}}
    if (not {'select', 'insert', 'update'} <= contract.actions
            or any(not fields <= columns.get(table, set()) for table, fields in required.items())):
        raise DomainError('SOURCE_OCR_RUNTIME_CONTRACT_INCOMPLETE')
    actor, role = settings.source_ocr_actor_id, settings.source_ocr_role
    if actor is None and role is None and len(contract.actor_ids) == len(contract.roles) == 1:
        actor, role = next(iter(contract.actor_ids)), next(iter(contract.roles))
    if actor not in contract.actor_ids or role not in contract.roles:
        raise DomainError('SOURCE_OCR_SCHEDULER_IDENTITY_REQUIRED')
    return contract


class SourceOCRPipeline:
    source_owned = True

    def __init__(self, container, contract, engine, recognizer):
        self.container, self.uow, self.settings = container, container.uow, container.settings
        self.contract, self.source_engine, self.recognizer = contract, engine, recognizer
        self.resolver, self.storage = container.source_resolver, container.storage
        self.heartbeat_timeout = self.settings.ocr_heartbeat_timeout_seconds
        self.staging_root = (self.settings.storage_root / '.source-ocr').resolve()
        self.writer = PostgreSQLSourceOCRWriter(engine, contract, authorize_identity=self.authorize_identity,
                                                authorize_created_version=self.authorize_created_version,
                                                authorize_namespace_version=self.authorize_namespace_version)
        self.queue = PostgreSQLSourceOCRQueue(self.writer, heartbeat_timeout=self.heartbeat_timeout,
                                              max_retries=self.settings.ocr_max_retries, max_active=self.settings.ocr_max_active)
        self.reader = PostgreSQLSourceOCRReader(engine, authorize=self.authorize_snapshot,
                                                lifecycle_allowed=lambda value: value in contract.lifecycles,
                                                statement_timeout_seconds=contract.statement_timeout_seconds)
        self.mongo = MongoExistingSourceOCRReader(container.documents.database)
        self.probe = SourceOCRProbe(self.mongo, authorize=self.authorize_snapshot, proof_provider=self.proof)
        self._reconcile_cursor = 0
        self._claim_cursor = 0
        self._schedule_cursor = 0
        from data_sync_etl.adapters.source.source_ocr_inventory import SourceOCRInventory
        self.inventory = SourceOCRInventory(self)

    def _scheduler_identity(self):
        actor, role = self.settings.source_ocr_actor_id, self.settings.source_ocr_role
        if actor is None and role is None and len(self.contract.actor_ids) == len(self.contract.roles) == 1:
            return next(iter(self.contract.actor_ids)), next(iter(self.contract.roles))
        return actor, role

    def authorize_created_version(self, identity):
        if (not self.contract.allow_etl_created_versions or identity.get('namespace') != self.contract.namespace
                or identity.get('actor_id') not in self.contract.actor_ids):
            return False
        with self.uow() as repo:
            version = repo.get('resource_versions', identity['master_version_id'])
            resource = repo.get('master_learning_resources', version['resource_id']) if version else None
            if (not resource or resource.get('source_origin') != 'POSTGRESQL'
                    or resource.get('created_by') != identity['actor_id'].removeprefix('master:')
                    or version.get('source_hash') != identity.get('source_hash')):
                return False
            receipt_reader = getattr(repo, 'source_version_writeback_receipt', None)
            receipt = (receipt_reader(resource.get('source_record_key'), identity['source_version_id'],
                                      identity['source_hash'], namespace=identity['namespace'])
                       if callable(receipt_reader) else None)
        return bool(receipt and receipt.get('requested_by') == identity['actor_id']
                    and receipt.get('source_user_id') == identity['source_user_id']
                    and receipt.get('source_namespace') == identity['namespace']
                    and receipt.get('source_resource_id') == resource.get('source_record_key')
                    and receipt.get('source_version_id') == identity['source_version_id']
                    and receipt.get('source_hash') == identity['source_hash'])

    def authorize_namespace_version(self, identity, *, reference=None, access=None, authority=None,
                                    source_snapshot=None):
        scope = self.contract.namespace_scope
        if (scope is None or identity.get('namespace') != self.contract.namespace
                or (identity.get('actor_id'), identity.get('role')) != (scope.service_actor_id, scope.service_role)
                or self._scheduler_identity() != (scope.service_actor_id, scope.service_role)
                or scope.service_actor_id not in self.contract.actor_ids or scope.service_role not in self.contract.roles):
            return False
        with self.uow() as repo:
            authority = authority or repo.source_ocr_authority(identity['master_version_id'], scope.service_actor_id)
            if authority is None:
                return False
            version, resource, user, rows = authority
            if (not resource or len(rows) != 1 or resource.get('source_origin') != 'POSTGRESQL'
                    or not user or user.get('source_namespace') != self.contract.namespace
                    or resource.get('source_namespace') != self.contract.namespace
                    or not resource.get('is_active') or resource_deleted(resource) or resource.get('source_missing_at')
                    or resource.get('publication_status') not in scope.publications
                    or version['source_hash'] != identity.get('source_hash')
                    or rows[0].get('source_version_id') != identity.get('source_version_id')
                    or rows[0].get('resource_id') != resource['id']
                    or rows[0].get('source_hash') != version['source_hash']
                    or rows[0].get('source_version_number') != version['version_number']
                    or rows[0].get('last_error_code') in ('SOURCE_VERSION_MUTATED', 'BLOCKED_SOURCE_MAPPING')
                    or SourceResourceCreateService._source_user_id(repo, scope.service_actor_id, user=user) != identity.get('source_user_id')):
                return False
        # The optional context comes only from this operation's freshly resolved
        # reference/principal. Nothing is cached across file IO, leases or turns.
        access = access or self._access(scope.service_actor_id, scope.service_role, resource['id'])
        if access.actor_id != scope.service_actor_id or not access.internal:
            return False
        self.container.search.authorize_source_processing_version(version['id'], access=access)
        registrations = [item for item in self.resolver.source_registry.registrations()
                         if item.enabled and item.namespace == self.contract.namespace and item.backend.lower() == 'postgresql']
        if (rows[0]['source_system'] not in {item.source_id for item in registrations}
                and not (rows[0]['source_system'] == 'postgresql' and len(registrations) == 1)):
            return False
        adapter = self.resolver._source_adapter(rows[0]['source_system'])
        if getattr(adapter, 'source_namespace', self.settings.source_namespace) != self.contract.namespace:
            return False
        source = source_snapshot
        if (reference is not None and reference.source_version_id == identity['source_version_id']
                and reference.target_version_id == identity['master_version_id']
                and reference.expected_sha256 == identity['source_hash']):
            source = getattr(reference, 'source_snapshot', None)
        if source is None:
            source = adapter.get_versions([identity['source_version_id']]).get(identity['source_version_id'])
        return bool(source and source.get('source_version_id') == identity['source_version_id']
                    and source.get('resource_id') == resource.get('source_record_key')
                    and source.get('source_version_number') == version['version_number']
                    and source.get('source_hash', '').lower() == identity['source_hash']
                    and source.get('resource_active', True) and source.get('resource_deleted_at') is None
                    and source.get('resource_publication_status') in scope.publications
                    and source.get('source_lifecycle_status') in scope.lifecycles)

    def version_allowed(self, identity, *, reference=None, access=None, authority=None, source_snapshot=None):
        return (identity['source_version_id'] in self.contract.version_ids
                or self.authorize_namespace_version(identity, reference=reference, access=access, authority=authority,
                                                    source_snapshot=source_snapshot)
                or self.authorize_created_version(identity))

    def created_version_allowed(self, master_version_id, actor_id, *, source_snapshot=None):
        with self.uow() as repo:
            version = repo.get('resource_versions', master_version_id)
            rows = repo.find('source_file_imports', {'target_version_id': master_version_id}, limit=2)
            if not version or len(rows) != 1:
                return False
            if rows[0]['source_version_id'] in self.contract.version_ids:
                return True
            source_user = SourceResourceCreateService._source_user_id(repo, actor_id)
        return self.version_allowed(dict(namespace=self.contract.namespace,
            master_version_id=master_version_id, source_version_id=rows[0]['source_version_id'],
            source_hash=version['source_hash'], actor_id=actor_id, source_user_id=source_user,
            role=self._scheduler_identity()[1] if actor_id == self._scheduler_identity()[0] else None),
            source_snapshot=source_snapshot)

    def status_allowed(self, source, namespace):
        if (namespace != self.contract.namespace
                or (source.get('source_version_id') not in self.contract.version_ids
                    and not self.contract.allow_etl_created_versions and self.contract.namespace_scope is None)
                or source.get('source_lifecycle_status') not in self.contract.lifecycles
                or not source.get('resource_active', True) or source.get('resource_deleted_at') is not None):
            return False
        caller = _PROCESSING_CALLER.get()
        if caller is not None:
            version_id, access = caller
            with self.uow() as repo:
                rows = repo.find('source_file_imports', {'target_version_id': version_id}, limit=2)
            role = 'ADMIN' if access.scope == 'authenticated_admin' else 'OPERATOR'
            if (len(rows) == 1 and rows[0]['source_version_id'] == source['source_version_id']
                    and access.actor_id in self.contract.actor_ids and role in self.contract.roles
                    and self.created_version_allowed(version_id, access.actor_id, source_snapshot=source)):
                self.container.search.authorize_source_processing_version(version_id, access=access)
                return True
        # Authorization of one file must not hydrate every imported version.
        # Ambiguous mappings remain denied, including across registered sources.
        with self.uow() as repo:
            rows = repo.find('source_file_imports', {'source_version_id': source['source_version_id']}, limit=2)
            if len(rows) != 1 or not rows[0].get('target_version_id'):
                return False
            version_id = rows[0]['target_version_id']
            state = self.state(version_id, repo)
        if state:
            identity = state['identity']
            if (identity['namespace'] != namespace or identity['source_version_id'] != source['source_version_id']
                    or not self.version_allowed(identity, source_snapshot=source)):
                return False
            access = self._access(identity['actor_id'], identity['role'], self._resource_id(version_id))
            self.container.search.authorize_source_processing_version(version_id, access=access)
            return True
        # A scheduler identity is explicitly configured and still needs a current grant.
        actor, role = self._scheduler_identity()
        if actor in self.contract.actor_ids and role in self.contract.roles:
            if not self.created_version_allowed(version_id, actor, source_snapshot=source):
                return False
            access = self._access(actor, role, self._resource_id(version_id))
            self.container.search.authorize_source_processing_version(version_id, access=access)
            return True
        return False

    def _resource_id(self, version_id):
        with self.uow() as repo:
            return repo.get('resource_versions', version_id)['resource_id']

    def state(self, version_id, repo=None):
        if repo is None:
            with self.uow() as reader:
                return self.state(version_id, reader)
        version = repo.get('resource_versions', version_id)
        return deepcopy((version.get('processing_options') or {}).get(STATE_KEY)) if version else None

    def _save(self, repo, version_id, state):
        version = repo.get('resource_versions', version_id, lock=True)
        options = dict(version.get('processing_options') or {})
        options[STATE_KEY] = deepcopy(state)
        repo.update('resource_versions', version_id, {'processing_options': options, 'updated_at': now()})

    def _reference(self, version_id):
        with self.uow() as repo:
            version = repo.get('resource_versions', version_id)
            resource = repo.get('master_learning_resources', version['resource_id']) if version else None
        if resource and resource.get('source_namespace') != self.contract.namespace:
            raise DomainError('SOURCE_OCR_NAMESPACE_MISMATCH')
        reference = self.resolver.resolve(version_id)
        if reference.ownership != 'SOURCE_BACKED':
            raise DomainError('SOURCE_OCR_SOURCE_OWNERSHIP_REQUIRED')
        self.resolver.require_content_capabilities(reference, index=True)
        source_id = self.resolver._capability_source_id(reference)
        registration = next(item for item in self.resolver.source_registry.registrations()
                            if item.source_id == source_id and item.enabled)
        if registration.namespace != self.contract.namespace or registration.backend.lower() != 'postgresql':
            raise DomainError('SOURCE_OCR_NAMESPACE_MISMATCH')
        return reference

    def authorize_identity(self, identity):
        if identity.get('actor_id') not in self.contract.actor_ids or identity.get('role') not in self.contract.roles:
            return False
        reference = self._reference(identity['master_version_id'])
        if (reference.source_version_id != identity['source_version_id']
                or reference.expected_sha256 != identity['source_hash']
                or identity['namespace'] != self.contract.namespace):
            return False
        with self.uow() as repo:
            authority = repo.source_ocr_authority(identity['master_version_id'], identity['actor_id'])
            if authority is None:
                return False
            version, resource, user, _ = authority
            source_user = SourceResourceCreateService._source_user_id(repo, identity['actor_id'], user=user)
            if user.get('source_namespace') != self.contract.namespace:
                return False
            state = deepcopy((version.get('processing_options') or {}).get(STATE_KEY))
        if not state or state['identity'] != identity or source_user != identity['source_user_id']:
            return False
        if state['fingerprint'] != self.fingerprint(version):
            raise DomainError('SOURCE_OCR_PROCESSING_IDENTITY_CHANGED')
        # Revalidate the persisted actor's grant every time, including worker recovery.
        access = self._access(identity['actor_id'], identity['role'], resource['id'])
        self.container.search.authorize_source_processing_version(identity['master_version_id'], access=access)
        return self.version_allowed(identity, reference=reference, access=access, authority=authority)

    def _access(self, actor_id, role, resource_id):
        # The server-side grant provider is reused; a stored role does not mint a grant.
        provider = getattr(self.container, 'source_ocr_access_provider', None)
        if provider is not None:
            access = provider(actor_id, role, resource_id)
            if access.actor_id != actor_id or not access.internal:
                raise DomainError('SOURCE_OCR_ACTOR_GRANT_REQUIRED')
            return access
        from data_sync_etl.security.master_service import MasterIdentityReader, eligible
        user = MasterIdentityReader(self.settings, engine=self.container.engine).read(
            user_id=actor_id.removeprefix('master:'))
        if role == 'ADMIN' and self.settings.admin_enabled and eligible(user) and user['role_code'] == 'ADMIN':
            return SearchAccess(scope='authenticated_admin', actor_id=actor_id)
        raise DomainError('SOURCE_OCR_ACTOR_GRANT_REQUIRED')

    def authorize_snapshot(self, snapshot):
        state = self.state(snapshot['master_version_id'])
        return bool(state and all(snapshot.get(key) == state['identity'][key]
                                  for key in ('namespace', 'source_version_id', 'master_version_id', 'source_hash'))
                    and self.authorize_identity(state['identity']))

    def fingerprint(self, version):
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as package_version
        revisions = {}
        for name in ('PyMuPDF', 'python-docx', 'openpyxl', 'python-pptx', 'Pillow'):
            try:
                revisions[name] = package_version(name)
            except PackageNotFoundError:
                revisions[name] = 'unavailable'
        if self.settings.ocr_engine == 'tesseract':
            self.recognizer.engine.check_runtime()
        options = {key: value for key, value in (version.get('processing_options') or {}).items()
                   if key in ('audio_track', 'subtitle_track',
                              'ocr_enabled', 'ocr_languages', 'visual_enabled')}
        payload = ['source-ocr-v1', self.settings.ocr_engine, self.settings.tesseract_languages,
                   getattr(self.recognizer.engine, '_version', None), revisions, options]
        return sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def proof(self, snapshot, job, page):
        state = snapshot.get('_source_ocr_state') or self.state(snapshot['master_version_id'])
        if not state or state.get('source_job_id') != job['id']:
            return None
        return state.get('pages', {}).get(str(page))

    def snapshot(self, state):
        identity = state['identity']
        result = self.reader.snapshot(**{key: identity[key] for key in
                                         ('namespace', 'source_version_id', 'master_version_id', 'source_hash')})
        return {**result, '_source_ocr_state': state}

    def create_job(self, version_id, triggered_by=None, *, fence=None, access=None):
        if access is None:
            actor, role = self._scheduler_identity()
            if not actor or not role:
                raise DomainError('SOURCE_OCR_SCHEDULER_ACTOR_REQUIRED')
            access = self._access(actor, role, self._resource_id(version_id))
        self.container.search.authorize_source_processing_version(version_id, access=access)
        token = _PROCESSING_CALLER.set((version_id, access))
        try:
            return self._create_job(version_id, triggered_by, fence=fence, access=access)
        except DomainError as exc:
            if str(exc) in ('SOURCE_OCR_EXTERNAL_JOB_REQUIRES_PROVENANCE', 'SOURCE_OCR_STRUCTURAL_MAPPING_REQUIRED'):
                self.record_scheduling_failure(version_id, exc)
            raise
        finally:
            _PROCESSING_CALLER.reset(token)

    def _create_job(self, version_id, triggered_by=None, *, fence=None, access=None):
        if triggered_by is not None:
            raise DomainError('SOURCE_OCR_AUTHENTICATED_ACTOR_REQUIRED')
        with self.uow.processing_lock(version_id) as acquired:
            if not acquired:
                existing_state = self.state(version_id)
                if (existing_state and existing_state.get('source_job_id') and access is not None
                        and existing_state['identity']['actor_id'] == access.actor_id
                        and self.authorize_identity(existing_state['identity'])):
                    public_id = stable_id('SJ', existing_state['identity']['namespace'], existing_state['source_job_id'])
                    with self.uow() as repo:
                        existing_job = repo.get('ocr_jobs', public_id)
                    if existing_job:
                        return existing_job
                raise DomainError('SOURCE_OCR_ALREADY_RUNNING')
            state = self.state(version_id)
            if access is None:
                actor, role = self._scheduler_identity()
                if not actor or not role:
                    raise DomainError('SOURCE_OCR_SCHEDULER_ACTOR_REQUIRED')
                reference = self._reference(version_id)
                access = self._access(actor, role, reference.resource_id)
            else:
                actor = access.actor_id
                role = 'ADMIN' if access.scope == 'authenticated_admin' else 'OPERATOR'
            self.container.search.authorize_source_processing_version(version_id, access=access)
            reference = self._reference(version_id)
            with self.uow() as repo:
                if fence:
                    fence(repo)
                version = repo.get('resource_versions', version_id, lock=True)
                existing = repo.find('ocr_jobs', {'resource_version_id': version_id}, limit=1)
                if existing and not state:
                    raise DomainError('SOURCE_OCR_LEGACY_VERSION_REQUIRES_EXPLICIT_PROVENANCE')
                source_user = SourceResourceCreateService._source_user_id(repo, actor)
                identity = dict(namespace=self.contract.namespace, master_version_id=version_id,
                                source_version_id=reference.source_version_id,
                                source_hash=reference.expected_sha256, actor_id=actor,
                                role=role, source_user_id=source_user)
                fingerprint = self.fingerprint(version)
                if state:
                    if state['identity'] != identity or state['fingerprint'] != fingerprint:
                        raise DomainError('SOURCE_OCR_PROCESSING_IDENTITY_CHANGED')
                else:
                    if (actor not in self.contract.actor_ids or role not in self.contract.roles
                            or not self.version_allowed(identity)):
                        raise DomainError('SOURCE_OCR_CONTRACT_DENIED')
                    state = dict(identity=identity, fingerprint=fingerprint, generation=1, pages={}, units={},
                                 job_create_intent_at=now().isoformat())
                    self._save(repo, version_id, state)
            if not state.get('source_job_id'):
                existing_snapshot = self.snapshot(state)
                existing_job = existing_snapshot['job']
                if existing_job:
                    intent = state.get('job_create_intent_at')
                    created = existing_job.get('created_at')
                    if (existing_job['status'] != 'PENDING' or existing_job['total_pages'] != 0
                            or existing_snapshot['pages'] or existing_job.get('triggered_by') != identity['source_user_id']
                            or not intent or not isinstance(created, datetime)
                            or created < datetime.fromisoformat(intent)):
                        raise DomainError('SOURCE_OCR_EXTERNAL_JOB_REQUIRES_PROVENANCE')
                if existing_snapshot['units']:
                    raise DomainError('SOURCE_OCR_STRUCTURAL_MAPPING_REQUIRED')
            job_id = self.writer.ensure_job(**identity)
            if state.get('source_job_id') not in (None, job_id):
                raise DomainError('SOURCE_OCR_JOB_CONFLICT')
            state['source_job_id'] = job_id
            with self.uow() as repo:
                self._save(repo, version_id, state)
            snapshot = self.snapshot(state)
            # Publish the stable public identity before slow authorized staging.
            # Concurrent HTTP calls can return this same pending projection;
            # SOURCE workers still require scheduled=True before claiming.
            self.sync(state, snapshot)
            if snapshot['job']['status'] == 'CANCELLED':
                raise DomainError('SOURCE_OCR_JOB_CANCELLED')
            result = self.probe.probe(snapshot, generation=state['generation'], processing_fingerprint=fingerprint)
            if result['status'] in ('UNVERIFIABLE',):
                raise DomainError('SOURCE_OCR_UNVERIFIABLE')
            if result['status'] == 'COMPLETE_VERIFIED':
                state['scheduled'] = True
                with self.uow() as repo:
                    self._save(repo, version_id, state)
                return self.sync(state, snapshot)
            if (snapshot['job']['status'] == 'FAILED'
                    or (state.get('scheduled') and snapshot['job']['status'] in ('PENDING', 'RUNNING')
                        and len(snapshot['pages']) == snapshot['job']['total_pages'])):
                return self.sync(state, snapshot)
            missing = set(result.get('missing_pages', ()))
            present_tasks = {page['page_num'] for page in snapshot['pages']}
            missing.update(page for page in result.get('recoverable_pages', ()) if page not in present_tasks)
            # Fully verified pages surviving a crash are completed by the ordinary
            # worker lease path; they never need staged bytes to be opened again.
            if snapshot['job']['total_pages'] == 0 or missing:
                state['scheduled'] = False
                with self.uow() as repo:
                    self._save(repo, version_id, state)
                self._stage(state, snapshot, missing)
            state['scheduled'] = True
            with self.uow() as repo:
                self._save(repo, version_id, state)
            return self.sync(state)

    def _stage(self, state, snapshot, missing):
        identity = state['identity']
        reference = self._reference(identity['master_version_id'])
        suffix = PurePosixPath(reference.filename).suffix.lower()
        validator = self.recognizer.formats.find_validator(reference.filename)
        if not validator.text_extraction_supported:
            raise DomainError('UNSUPPORTED_PROCESSING_FORMAT')
        with self.resolver.open_verified_stream(reference, index=True) as stream:
            data = stream.read()
        if sha256(data).hexdigest() != identity['source_hash']:
            raise DomainError('SOURCE_HASH_MISMATCH')
        import pymupdf
        try:
            document = pymupdf.open(stream=data, filetype='pdf') if suffix == '.pdf' else None
        except Exception:
            raise DomainError('INVALID_DOCUMENT_CONTENT') from None
        try:
            if document is not None and document.needs_pass:
                raise DomainError('INVALID_DOCUMENT_CONTENT')
            total = len(document) if document is not None else 1
            self.writer.set_page_count(**identity, source_job_id=state['source_job_id'], total_pages=total)
            if snapshot['job']['total_pages'] == 0:
                missing = set(range(1, total + 1))
            self.queue.prepare_missing(identity, state['source_job_id'], missing)
            root = self.staging_root / stable_id('SV', identity['namespace'], identity['source_version_id'])
            root.mkdir(parents=True, exist_ok=True)
            for page in sorted(missing):
                path = root / f'{page}{suffix}'
                if document is not None:
                    with pymupdf.open() as part:
                        part.insert_pdf(document, from_page=page - 1, to_page=page - 1)
                        payload = part.tobytes(no_new_id=True)
                else:
                    payload = data
                checksum = sha256(payload).hexdigest()
                prior = state['pages'].get(str(page), {})
                if prior.get('staging_sha256') not in (None, checksum):
                    raise DomainError('SOURCE_OCR_STAGING_CONFLICT')
                path.write_bytes(payload)
                state['pages'][str(page)] = {**prior, 'staging_sha256': checksum, 'suffix': suffix}
                with self.uow() as repo:
                    self._save(repo, identity['master_version_id'], state)
                self.writer.ensure_page_task(**identity, source_job_id=state['source_job_id'], page_num=page,
                                             staging_path=path, staging_root=self.staging_root)
        finally:
            if document is not None:
                document.close()

    def states(self, *, active_only=False):
        with self.uow() as repo:
            return deepcopy(repo.source_ocr_states(self.contract.namespace, active_only=active_only))

    def sync(self, state, snapshot=None):
        snapshot = snapshot or self.snapshot(state)
        identity, source = state['identity'], snapshot['job']
        if not source or source['id'] != state['source_job_id']:
            raise DomainError('SOURCE_OCR_JOB_CONFLICT')
        if source['status'] == 'CANCELLED':
            raise DomainError('SOURCE_OCR_JOB_CANCELLED')
        master_id = stable_id('SJ', identity['namespace'], source['id'])
        with self.uow() as repo:
            version = repo.get('resource_versions', identity['master_version_id'], lock=True)
            if self.state(version['id'], repo) != state:
                raise DomainError('SOURCE_OCR_STATE_CHANGED')
            values = {key: source[key] for key in ('status', 'total_pages', 'completed_pages', 'failed_pages',
                                                  'started_at', 'completed_at', 'updated_at')}
            # MASTER ocr_jobs.triggered_by stores the canonical MASTER user ID
            # (the FK identity), while authorization/checkpoints retain the
            # full `master:<id>` application principal.
            values.update(resource_version_id=version['id'],
                          triggered_by=identity['actor_id'].removeprefix('master:'))
            existing = repo.get('ocr_jobs', master_id)
            if existing:
                if existing['resource_version_id'] != version['id']:
                    raise DomainError('SOURCE_OCR_MASTER_ID_CONFLICT')
                repo.update('ocr_jobs', master_id, values)
            else:
                if repo.find('ocr_jobs', {'resource_version_id': version['id']}, limit=1):
                    raise DomainError('SOURCE_OCR_MASTER_ID_CONFLICT')
                repo.insert('ocr_jobs', dict(id=master_id, **values))
            for page in snapshot['pages']:
                page_id = stable_id('SP', identity['namespace'], source['id'], page['id'])
                values = {key: page[key] for key in ('page_num', 'status', 'retry_count', 'updated_at',
                                                    'started_at', 'heartbeat_at', 'completed_at')}
                values.update(job_id=master_id, local_image_path=None)
                current = repo.get('ocr_page_tasks', page_id)
                if current:
                    if current['job_id'] != master_id or current['page_num'] != page['page_num']:
                        raise DomainError('SOURCE_OCR_MASTER_ID_CONFLICT')
                    repo.update('ocr_page_tasks', page_id, values)
                else:
                    repo.insert('ocr_page_tasks', dict(id=page_id, **values))
            for source_unit in snapshot['units']:
                target = next((unit_id for unit_id, proof in state['units'].items()
                               if proof.get('source_unit_id') == source_unit['id']), None)
                if target is None:
                    # Unknown SOURCE structures cannot be grafted onto a derived tree
                    # merely because mongo_id or an ID string happens to match.
                    # SOURCE may commit a structure before the MASTER transaction.
                    # Postprocess reconstructs its deterministic mapping on replay.
                    continue
                current = repo.get('content_units', target)
                if not current or current['resource_version_id'] != version['id']:
                    raise DomainError('SOURCE_OCR_STRUCTURAL_MAPPING_REQUIRED')
                parent = (next((unit_id for unit_id, proof in state['units'].items()
                                if proof.get('source_unit_id') == source_unit['parent_unit_id']), None)
                          if source_unit['parent_unit_id'] is not None else None)
                if source_unit['parent_unit_id'] is not None and parent is None:
                    raise DomainError('SOURCE_OCR_STRUCTURAL_MAPPING_REQUIRED')
                values = {key: source_unit[key] for key in ('unit_type', 'title', 'sequence_no', 'page_from', 'page_to',
                                                           'review_status', 'retrieval_eligible')}
                values['parent_unit_id'] = parent
                if any(current[key] != value for key, value in values.items()):
                    repo.update('content_units', target, {**values, 'updated_at': source_unit['updated_at']})
            # File ownership remains SOURCE_BACKED; INGESTED denotes derived content.
            if source['total_pages'] and version['lifecycle_status'] == 'METADATA_ONLY':
                repo.update('resource_versions', version['id'], {'lifecycle_status': 'INGESTED'})
            return repo.get('ocr_jobs', master_id)

    def _projection_terminal(self, state):
        # A terminal projection is a scheduling hint. Reconciliation still polls
        # SOURCE and makes externally retried jobs runnable again.
        with self.uow() as repo:
            job = repo.get('ocr_jobs', stable_id('SJ', state['identity']['namespace'], state['source_job_id']))
        return bool(job and job['status'] in ('COMPLETED', 'FAILED', 'CANCELLED'))

    def claim(self, worker_id):
        states = self.states(active_only=True)
        start = self._claim_cursor % len(states) if states else 0
        for index, (version_id, state) in enumerate(states[start:] + states[:start]):
            if not state or not state.get('source_job_id') or not state.get('scheduled'):
                continue
            try:
                task = self.queue.claim(state['identity'], state['source_job_id'], worker_id,
                    allow_claim=lambda version_id=version_id: bool((self.state(version_id) or {}).get('scheduled')))
                self.sync(self.state(version_id))
            except Exception as exc:
                from data_sync_etl.logging import event
                safe = source_sql_error(exc)
                code = str(safe)
                if (not code or len(code) > 80 or not code.replace('_', '').isalnum()
                        or not code.isupper()):
                    code = 'SOURCE_OCR_CLAIM_UNAVAILABLE'
                event(stage='source_ocr', action='CLAIM_UNAVAILABLE', error_type=type(safe).__name__,
                      error_code=code)
                continue
            if task:
                self._claim_cursor = (start + index + 1) % len(states)
                return {**task, 'master_version_id': version_id,
                        'job_id': stable_id('SJ', state['identity']['namespace'], state['source_job_id'])}
        return None

    def heartbeat(self, task):
        state = self.state(task['master_version_id'])
        return self.queue.transition(state['identity'], task, heartbeat=True)

    def verified_page(self, state, page):
        identity = state['identity']
        doc = self.mongo.ocr_page(version_id=identity['source_version_id'], job_id=state['source_job_id'], page_num=page)
        if doc is None:
            return None
        proof = state['pages'].get(str(page), {})
        if (proof.get('generation') != state['generation'] or proof.get('processing_fingerprint') != state['fingerprint']
                or doc.projection_generation != state['generation']
                or doc.resource_version_id != identity['source_version_id'] or doc.job_id != state['source_job_id']
                or doc.source_checksum != identity['source_hash']
                or doc.engine_name != proof.get('engine_name') or doc.engine_version != proof.get('engine_version')
                or sha256(doc.raw_text.encode()).hexdigest() != proof.get('raw_sha256')):
            raise DomainError('SOURCE_OCR_PAGE_CONFLICT')
        return doc

    def recognize(self, task):
        state = self.state(task['master_version_id'])
        if not self.authorize_identity(state['identity']):
            raise DomainError('SOURCE_OCR_UNAUTHORIZED')
        existing = self.verified_page(state, task['page_num'])
        if existing is not None:
            return None, None
        proof = state['pages'][str(task['page_num'])]
        path = Path(task['local_image_path']).resolve(strict=True)
        if not path.is_relative_to(self.staging_root):
            raise DomainError('SOURCE_OCR_STAGING_INVALID')
        payload = path.read_bytes()
        if sha256(payload).hexdigest() != proof['staging_sha256']:
            raise DomainError('SOURCE_OCR_STAGING_CONFLICT')
        with self.uow() as repo:
            version = repo.get('resource_versions', task['master_version_id'])
        if path.suffix.lower() in ('.mp3', '.mp4'):
            self.recognizer.formats.find_validator(path.name).validate_path(path)
            result, _ = self.recognizer._recognize_path({'page_num': 1}, version, path,
                                                       source_directory=path.parent)
        else:
            result = self.recognizer._recognize_bytes({'page_num': 1}, version, path.name, payload, allow_empty=True)
        result = paired_result(result, version['id'], task['page_num'],
                               {'target_language': self.recognizer.default_target_language,
                                **(version.get('processing_options') or {})})
        return result, None

    def succeed(self, task, result, image_path=None):
        state = self.state(task['master_version_id'])
        self.queue.transition(state['identity'], task, complete=True,
                              before_complete=lambda: self._persist_page(task, result))
        self.sync(self.state(task['master_version_id']))

    def _persist_page(self, task, result):
        state = self.state(task['master_version_id'])
        identity, page = state['identity'], task['page_num']
        if not self.authorize_identity(identity):
            raise DomainError('SOURCE_OCR_UNAUTHORIZED')
        if result is not None:
            proof = dict(state['pages'][str(page)], generation=state['generation'],
                         processing_fingerprint=state['fingerprint'], engine_name=result.engine_name,
                         engine_version=result.engine_version, raw_sha256=sha256(result.text.encode()).hexdigest(),
                         result_metadata=self._metadata(result.metadata))
            # Durable intent precedes immutable Mongo insertion. It contains checksums
            # and extraction locators, never raw/normalized text or file capabilities.
            with self.uow() as repo:
                repo.get('resource_versions', task['master_version_id'], lock=True)
                current = self.state(task['master_version_id'], repo)
                previous = current['pages'][str(page)]
                if previous.get('raw_sha256') not in (None, proof['raw_sha256']):
                    raise DomainError('SOURCE_OCR_PAGE_CONFLICT')
                current['pages'][str(page)] = proof
                self._save(repo, task['master_version_id'], current)
            state = self.state(task['master_version_id'])
            stamp = now()
            self.container.documents.insert_source_ocr_page(dict(
                _id=f"{state['source_job_id']}:{page}", job_id=state['source_job_id'],
                resource_version_id=identity['source_version_id'], page_num=page, raw_text=result.text,
                char_count=len(result.text), confidence=float(result.confidence) if result.confidence is not None else None,
                engine_name=result.engine_name, engine_version=result.engine_version,
                source_checksum=identity['source_hash'], projection_generation=state['generation'],
                created_at=stamp, updated_at=stamp))
        if self.verified_page(state, page) is None:
            raise DomainError('SOURCE_OCR_PAGE_READBACK_FAILED')

    @staticmethod
    def _metadata(metadata):
        from data_sync_etl.application.source_ocr_text import metadata_without_raw
        return metadata_without_raw(metadata)

    def ensure_structural_unit(self, state, unit):
        identity = state['identity']
        parent = (state['units'].get(unit['parent_unit_id'], {}).get('source_unit_id')
                  if unit['parent_unit_id'] else None)
        if unit['parent_unit_id'] is not None and parent is None:
            raise DomainError('SOURCE_OCR_STRUCTURAL_MAPPING_REQUIRED')
        fields = {'id', 'mongo_id', 'resource_version_id', 'parent_unit_id', 'unit_type', 'title',
                  'sequence_no', 'page_from', 'page_to', 'review_status', 'retrieval_eligible',
                  'created_at', 'updated_at'}
        with self.source_engine.begin() as conn:
            self.queue._guard(conn, identity)
            self.queue._job(conn, identity, state['source_job_id'])
            lifecycle = conn.execute(text('SELECT lifecycle_status FROM public.resource_versions WHERE id=:id'),
                                     {'id': identity['source_version_id']}).scalar_one()
            permission = dict(namespace=identity['namespace'], table='content_units', fields=fields,
                              role=identity['role'], actor_id=identity['actor_id'],
                              source_version_id=identity['source_version_id'], lifecycle=lifecycle)
            if not all(self.writer.contract_for(identity).allows(**permission, action=action)
                       for action in ('select', 'insert')):
                raise DomainError('SOURCE_OCR_CONTRACT_DENIED')
            values = {key: unit[key] for key in ('unit_type', 'title', 'sequence_no', 'page_from', 'page_to',
                                                'review_status', 'retrieval_eligible')}
            source_review = {'UNREVIEWED': 'PENDING', 'APPROVED': 'APPROVED', 'REVIEWED': 'APPROVED', 'REJECTED': 'REJECTED'}
            if values['review_status'] not in source_review:
                raise DomainError('SOURCE_OCR_REVIEW_STATUS_INVALID')
            values['review_status'] = source_review[values['review_status']]
            values.update(mongo_id=unit['id'], resource_version_id=identity['source_version_id'],
                          parent_unit_id=parent)
            rows = conn.execute(text('SELECT * FROM public.content_units WHERE resource_version_id=:version '
                                     'AND mongo_id=:mongo LIMIT 2 FOR UPDATE'),
                                {'version': identity['source_version_id'], 'mongo': unit['id']}).mappings().all()
            if len(rows) > 1:
                raise DomainError('SOURCE_OCR_STRUCTURAL_CONFLICT')
            existing = rows[0] if rows else None
            if existing:
                if any(existing[key] != value for key, value in values.items()):
                    raise DomainError('SOURCE_OCR_STRUCTURAL_CONFLICT')
                source_id = existing['id']
            else:
                source_id = self.writer.next_id(conn, 'CUN')
                conn.execute(text('INSERT INTO public.content_units (id,mongo_id,resource_version_id,parent_unit_id, '
                                  'unit_type,title,sequence_no,page_from,page_to,review_status,retrieval_eligible,created_at,updated_at) '
                                  'VALUES (:id,:mongo_id,:resource_version_id,:parent_unit_id,:unit_type,:title, '
                                  ':sequence_no,:page_from,:page_to,:review_status,:retrieval_eligible,:stamp,:stamp)'),
                             {**values, 'id': source_id, 'stamp': now()})
        return source_id

    def fail(self, task, error=None):
        code = str(error) if isinstance(error, DomainError) else 'SOURCE_OCR_PAGE_FAILED'
        if not code.replace('_', '').isalnum() or len(code) > 80:
            code = 'SOURCE_OCR_PAGE_FAILED'
        state = self.state(task['master_version_id'])
        try:
            self.queue.transition(state['identity'], task, error=code)
            self.sync(self.state(task['master_version_id']))
        except DomainError as exc:
            if str(exc) not in ('SOURCE_OCR_LEASE_LOST', 'SOURCE_OCR_UNAUTHORIZED'):
                raise

    def recover_stale(self):
        recovered = 0
        for _, state in self.states(active_only=True):
            if state and state.get('source_job_id') and not self._projection_terminal(state):
                try:
                    recovered += self.queue.recover(state['identity'], state['source_job_id'])
                    self.sync(state)
                except Exception:
                    from data_sync_etl.logging import event
                    event(stage='source_ocr', action='RECOVERY_UNAVAILABLE')
        return {'recovered': recovered}

    def reconcile_once(self):
        states = [(version_id, state) for version_id, state in self.states() if state and state.get('source_job_id')]
        if not states:
            return False
        index = self._reconcile_cursor % len(states)
        self._reconcile_cursor += 1
        _, state = states[index]
        repair_attempted = False
        try:
            snapshot = self.snapshot(state)
            with self.uow() as repo:
                retries = repo.find('etl_stage_runs', {'resource_version_id': state['identity']['master_version_id'],
                                   'stage': 'OCR_RETRY', 'status': 'RUNNING'}, order=('created_at', 'id'), limit=1)
            if retries:
                request = retries[0]
                if snapshot['job']['status'] == 'FAILED' and snapshot['job']['updated_at'] <= request['created_at']:
                    self.queue.retry(state['identity'], state['source_job_id'])
                    snapshot = self.snapshot(state)
                with self.uow() as repo:
                    repo.update('etl_stage_runs', request['id'], {'status': 'COMPLETED', 'finished_at': now()})
            self.sync(state, snapshot)
            if snapshot['job']['status'] == 'COMPLETED':
                proof = self.probe.probe(snapshot, generation=state['generation'],
                                         processing_fingerprint=state['fingerprint'])
                if proof['status'] == 'PARTIAL' and self.scheduling_row(state['identity']['master_version_id']):
                    identity = state['identity']
                    access = self._access(identity['actor_id'], identity['role'],
                                          self._resource_id(identity['master_version_id']))
                    repair_attempted = True
                    self.create_job(identity['master_version_id'], access=access)
        except Exception as exc:
            if repair_attempted:
                self.record_scheduling_failure(state['identity']['master_version_id'], exc)
            return False
        return False  # Polling alone must never starve pending finalization/index work.

    def schedule_authorized_source_job(self, *, limit=50):
        if not self._scheduler_identity()[0]:
            return False
        # Persisted ready flags survive cursor movement/restart. They are only
        # hints: scheduling and writer revalidate live authority before execution.
        candidates = self.inventory.ready(limit=min(10, limit))
        if not candidates:
            candidates = self.inventory.once(limit=1, prioritize_new=True)
        for version_id in candidates:
            try:
                if not self.scheduling_row(version_id):
                    self.inventory.defer(version_id)
                    continue
                self.create_job(version_id)
                self.inventory.dispatched(version_id)
                return True
            except Exception as exc:
                self.record_scheduling_failure(version_id, exc)
                self.inventory.defer(version_id)
                continue
        return False

    def scheduling_row(self, version_id):
        with self.uow() as repo:
            rows = repo.find('source_file_imports', {'target_version_id': version_id}, limit=2)
        if len(rows) != 1:
            return None
        row = rows[0]
        if (not self.created_version_allowed(version_id, self._scheduler_identity()[0])
                or row.get('attempts', 0) >= self.settings.source_file_import_max_attempts
                or row.get('status') in ('FAILED', 'RETRY_EXHAUSTED', 'CANCELLED')
                or (row.get('next_retry_at') is not None and row['next_retry_at'] > now())):
            return None
        return row

    def record_scheduling_failure(self, version_id, error):
        if isinstance(error, DomainError) and str(error) == 'SOURCE_OCR_ALREADY_RUNNING':
            return
        code = str(error) if isinstance(error, DomainError) else 'SOURCE_OCR_SCHEDULE_FAILED'
        code = {'SOURCE_OCR_LEGACY_VERSION_REQUIRES_EXPLICIT_PROVENANCE':
                'SOURCE_OCR_LEGACY_PROVENANCE_REQUIRED'}.get(code, code)
        # Existing MASTER schema limits source_file_imports.last_error_code to 50.
        # A diagnostic must never prevent the scheduler from handling the next item.
        if len(code) > 50 or not code.replace('_', '').isalnum():
            code = 'SOURCE_OCR_SCHEDULE_FAILED'
        with self.uow() as repo:
            rows = repo.find('source_file_imports', {'target_version_id': version_id}, limit=2, lock=True)
            if len(rows) != 1:
                return
            row = rows[0]
            attempts = row.get('attempts', 0) + 1
            retry = attempts < self.settings.source_file_import_max_attempts
            delay = min(3600, self.settings.source_file_import_retry_seconds * 2 ** min(attempts - 1, 10))
            repo.update('source_file_imports', row['id'], {
                'attempts': attempts, 'status': 'RETRY_WAIT' if retry else 'RETRY_EXHAUSTED',
                'last_error_code': code, 'next_retry_at': now() + timedelta(seconds=delay) if retry else None,
                'updated_at': now()})

    def scheduler_authorized(self, version_id):
        actor, role = self._scheduler_identity()
        if not actor or not role:
            return False
        try:
            access = self._access(actor, role, self._resource_id(version_id))
            self.container.search.authorize_source_processing_version(version_id, access=access)
            return True
        except Exception:
            return False

    def cleanup_staging(self, state):
        root = self.staging_root / stable_id('SV', state['identity']['namespace'], state['identity']['source_version_id'])
        for number, proof in state['pages'].items():
            if not number.isdigit() or proof.get('suffix') not in ('.pdf', '.docx', '.xlsx', '.pptx', '.txt', '.csv', '.png', '.jpg', '.jpeg', '.mp3', '.mp4'):
                continue
            path = root / f"{number}{proof['suffix']}"
            try:
                resolved = path.resolve(strict=True)
                if (resolved.is_relative_to(self.staging_root) and not path.is_symlink()
                        and sha256(resolved.read_bytes()).hexdigest() == proof.get('staging_sha256')):
                    resolved.unlink()
            except OSError:
                continue


def wire_source_ocr(container, contract, writer_url):
    if not writer_url or container.content_reader is None:
        raise DomainError('SOURCE_OCR_DEPENDENCIES_REQUIRED')
    with container.engine.connect() as conn:
        if conn.execute(text('SELECT version_num FROM alembic_version')).scalars().all() != ['0020']:
            raise DomainError('SOURCE_OCR_MASTER_MIGRATION_0020_REQUIRED')
    registrations = [item for item in container.source_registry.registrations()
                     if item.enabled and item.namespace == contract.namespace and item.backend.lower() == 'postgresql']
    if len(registrations) != 1 or not all(getattr(registrations[0].capabilities, capability)
            for capability in ('read_content', 'process_content', 'index_derived_content', 'exact_version_lookup')):
        raise DomainError('SOURCE_OCR_SOURCE_CAPABILITIES_REQUIRED')
    try:
        collections = set(container.documents.database.list_collection_names())
    except PyMongoError:
        raise DomainError('SOURCE_MONGO_UNAVAILABLE') from None
    if collections != {'users', 'learning_resources', 'ocr_pages', 'mongo_content_units', 'content_bags'}:
        raise DomainError('SOURCE_OCR_MONGO_TOPOLOGY_INVALID')
    try:
        writer_identity = make_url(writer_url)
        expected_database = make_url(container.settings.source_database_url).database
        if writer_identity.get_backend_name() != 'postgresql' or writer_identity.database != expected_database:
            raise DomainError('SOURCE_OCR_DATABASE_IDENTITY_MISMATCH')
        engine = make_engine(writer_url)
        container.source_ocr_engine = engine
        with engine.connect() as connection:
            if connection.execute(text('SELECT current_database()')).scalar_one() != expected_database:
                raise DomainError('SOURCE_OCR_DATABASE_IDENTITY_MISMATCH')
    except SQLAlchemyError as exc:
        raise source_sql_error(exc) from None
    pipeline = SourceOCRPipeline(container, contract, engine, container.ocr)
    container.source_resolver.source_ocr_status_policy = pipeline.status_allowed
    from data_sync_etl.application.source_ocr_text import SourceOCRText, SourceOnlyProjector
    container.ocr = pipeline
    container.ocr_text_store = SourceOCRText(pipeline, LegacyOCRText())
    container.postprocess.text_store = container.ocr_text_store
    container.indexing.text_store = container.ocr_text_store
    container.bilingual.text_store = container.ocr_text_store
    container.orchestrator.ocr = pipeline
    container.orchestrator.text_store = container.ocr_text_store
    container.processing_job_detail.text_store = container.ocr_text_store
    container.processing_finalizer.text_store = container.ocr_text_store
    container.mongo_projector = SourceOnlyProjector(pipeline, container.mongo_projector)
    container.processing_finalizer.projector = container.mongo_projector
    container.process_version.ocr = pipeline
    container.retry_processing_job.source_pipeline = pipeline
    container.process_version.text_store = container.ocr_text_store
