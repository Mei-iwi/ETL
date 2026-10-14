"""Reconcile committed Master identities; extension data has no source contract yet."""

from pydantic import ValidationError

from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.mongo_documents import ResourceDescriptionDocument, UserProfileDocument
from data_sync_etl.logging import event
from data_sync_etl.ports.mongo_foundation import MetadataDocumentPort
from data_sync_etl.ports.repositories import UnitOfWork

MODELS = {'users': UserProfileDocument, 'resources': ResourceDescriptionDocument}


class MasterMetadataProjection:
    def __init__(self, uow: UnitOfWork, documents: MetadataDocumentPort | None,
                 *, batch_size=100, identity_bootstrap=False, identity_ids=None, identity_guard=None):
        if type(batch_size) is not int or not 1 <= batch_size <= 1000:
            raise ValueError('Invalid metadata projection batch size')
        self.uow, self.documents, self.batch_size = uow, documents, batch_size
        self.identity_bootstrap = identity_bootstrap
        self.identity_ids = {stream: frozenset(ids) for stream, ids in (identity_ids or {}).items()}
        self.identity_guard = identity_guard

    def run(self, stream='all'):
        if stream != 'all' and stream not in MODELS:
            raise DomainError('Unknown metadata projection stream')
        return {name: self._stream(name) for name in (MODELS if stream == 'all' else [stream])}

    def _stream(self, stream):
        report = dict(scanned=0, projected=0, unchanged=0, skipped_no_extension=0,
                      skipped_unapproved=0, conflicts=0, failed=0, mode='OPTIONAL_IDENTITY_BOOTSTRAP'
                      if self.identity_bootstrap else 'EXTENSION_SYNC',
                      reason='IDENTITY_ONLY' if self.identity_bootstrap else 'SKIPPED_NO_EXTENSION')
        with self.uow.processing_lock('master-mongo:' + stream) as acquired:
            if not acquired:
                return {**report, 'status': 'BUSY'}
            approved = self.identity_ids.get(stream, ())
            if not self.identity_bootstrap or not approved:
                # No extension source/writer exists. COUNT assesses eligibility without
                # fetching Master identities/timestamps on every Auto run or making RPCs.
                with self.uow() as repo:
                    count = repo.count('master_users' if stream == 'users' else 'master_learning_resources', {})
                report['scanned'] = count
                if self.identity_bootstrap:
                    report.update(skipped_unapproved=count, status='NO_APPROVED_IDS', reason='NO_APPROVED_IDS')
                else:
                    report.update(skipped_no_extension=count, status='PARTIAL_SOURCE_FIELDS')
                event(stage='master_metadata_projection', stream=stream,
                      action=report['status'], coverage=report)
                return report
            after = None
            while True:
                # Payload is read only from committed Master; transaction ends before RPCs.
                # The optional infrastructure guard checks source references, not payload fields.
                with self.uow() as repo:
                    rows = repo.master_metadata_page(stream, after, self.batch_size)
                if not rows:
                    break
                requested_ids = [r['id'] for r in rows if r['id'] in approved]
                try:
                    conflicts = (self.identity_guard(stream, requested_ids)
                                 if requested_ids and self.identity_guard else set())
                except Exception:
                    # Failed reference verification cannot grant permission to create IDs.
                    report['status'] = 'BLOCKED_MONGO_ID_MAPPING'
                    report['reason'] = 'IDENTITY_GUARD_UNAVAILABLE'
                    report['conflicts'] += len(requested_ids)
                    report['skipped_unapproved'] += len(rows) - len(requested_ids)
                    report['scanned'] += len(rows)
                    event(stage='master_metadata_projection', stream=stream,
                          action=report['status'], coverage=report)
                    return report
                for row in rows:
                    report['scanned'] += 1
                    if row['id'] not in approved:
                        report['skipped_unapproved'] += 1
                        continue
                    try:
                        document = MODELS[stream](
                            _id=row['id'], created_at=row['created_at'], updated_at=row['updated_at'],
                        )
                        # A future repository exposing a reference must not silently remap it.
                        if row.get('mongo_id') not in (None, row['id']) or row['id'] in conflicts:
                            report['conflicts'] += 1
                            continue
                        if self.documents is None:
                            raise RuntimeError('Metadata store unavailable')
                        existing = (self.documents.get_user(document.id) if stream == 'users'
                                    else self.documents.get_resource(document.id))
                        if existing is not None:
                            # Mongo owns extensions and all timestamps of an existing document.
                            report['unchanged'] += 1
                            continue
                        inserted = (self.documents.ensure_user_identity(document) if stream == 'users'
                                    else self.documents.ensure_resource_identity(document))
                        report['projected' if inserted else 'unchanged'] += 1
                    except ValidationError:
                        report['failed'] += 1
                    except DomainError:
                        # Invalid legacy Mongo document needs operator review, not replacement.
                        report['conflicts'] += 1
                    except Exception:
                        report['failed'] += 1
                        # An outage is retryable by full reconciliation; avoid N timeout RPCs.
                        report['status'] = 'FAILED'
                        report['reason'] = 'RECONCILE_REQUIRED'
                        event(stage='master_metadata_projection', stream=stream,
                              action='FAILED', coverage=report)
                        return report
                after = rows[-1]['id']
                if len(rows) < self.batch_size:
                    break
        report['status'] = ('FAILED' if report['failed'] else 'BLOCKED_MONGO_ID_MAPPING'
                            if report['conflicts'] else 'READY_IDENTITY_ONLY'
                            if report['projected'] or report['unchanged'] else 'NO_APPROVED_IDS')
        if report['status'] == 'NO_APPROVED_IDS':
            report['reason'] = 'NO_APPROVED_IDS'
        event(stage='master_metadata_projection', stream=stream,
              action=report['status'], coverage=report)
        return report
