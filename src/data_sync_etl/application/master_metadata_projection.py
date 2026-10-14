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
                 *, batch_size=100, identity_bootstrap=False, identity_ids=None, identity_guard=None,
                 identity_scope='approved_ids'):
        if type(batch_size) is not int or not 1 <= batch_size <= 1000:
            raise ValueError('Invalid metadata projection batch size')
        self.uow, self.documents, self.batch_size = uow, documents, batch_size
        self.identity_bootstrap = identity_bootstrap
        self.identity_ids = {stream: frozenset(ids) for stream, ids in (identity_ids or {}).items()}
        self.identity_guard = identity_guard
        if identity_scope not in ('approved_ids', 'source_verified'):
            raise ValueError('Invalid metadata identity scope')
        self.identity_scope = identity_scope

    def run(self, stream='all'):
        if stream != 'all' and stream not in MODELS:
            raise DomainError('Unknown metadata projection stream')
        return {name: self._stream(name) for name in (MODELS if stream == 'all' else [stream])}

    def _stream(self, stream):
        report = dict(scanned=0, projected=0, unchanged=0, skipped_no_extension=0,
                      skipped_unapproved=0, skipped_not_source_backed=0, conflicts=0, failed=0,
                      scope=self.identity_scope, mode='OPTIONAL_IDENTITY_BOOTSTRAP'
                      if self.identity_bootstrap else 'EXTENSION_SYNC',
                      reason='IDENTITY_ONLY' if self.identity_bootstrap else 'SKIPPED_NO_EXTENSION')
        with self.uow.metadata_projection_lock(stream) as acquired:
            if not acquired:
                return {**report, 'status': 'BUSY'}
            approved = self.identity_ids.get(stream, ())
            if not self.identity_bootstrap or (not approved and self.identity_scope == 'approved_ids'):
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
                requested_ids = [r['id'] for r in rows if self.identity_scope == 'source_verified' or r['id'] in approved]
                try:
                    if requested_ids and self.identity_scope == 'source_verified' and self.identity_guard is None:
                        raise RuntimeError('Source verifier unavailable')
                    references = (self.identity_guard(stream, requested_ids)
                                  if requested_ids and self.identity_guard else {})
                    if not isinstance(references, dict):
                        raise RuntimeError('Source verifier contract invalid')
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
                    if self.identity_scope == 'approved_ids' and row['id'] not in approved:
                        report['skipped_unapproved'] += 1
                        continue
                    try:
                        source_backed = row['id'] in references
                        if self.identity_scope == 'source_verified' and not source_backed:
                            report['skipped_not_source_backed'] += 1
                            continue
                        current = row['mongo_id']
                        source_ref = references.get(row['id'])
                        # Declared non-PK references are valid. A mismatch with committed
                        # Master, or an ambiguous source-NULL/non-PK Master link, is not.
                        if source_backed and (
                            (source_ref is not None and source_ref != current)
                            or (source_ref is None and current not in (None, row['id']))
                        ):
                            report['conflicts'] += 1
                            continue
                        target = current or row['id']  # Fallback ONLY in explicitly enabled scopes.
                        document = MODELS[stream](
                            _id=target, created_at=row['created_at'], updated_at=row['updated_at'],
                        )
                        if self.documents is None:
                            raise RuntimeError('Metadata store unavailable')
                        existing = (self.documents.get_user(document.id) if stream == 'users'
                                    else self.documents.get_resource(document.id))
                        # Reserve the reference in a short row-locked transaction BEFORE
                        # insertion. Validate the existing target before reserving it.
                        # Downtime may leave a pending reference, never a second ID.
                        # Source is read-only; NULL sync never clears this local fallback.
                        with self.uow() as repo:
                            repo.reserve_master_mongo_identity(stream, row['id'], current, target)
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
                            if report['projected'] or report['unchanged'] else
                            'NO_SOURCE_BACKED_IDS' if self.identity_scope == 'source_verified' else 'NO_APPROVED_IDS')
        if report['status'] in ('NO_APPROVED_IDS', 'NO_SOURCE_BACKED_IDS'):
            report['reason'] = report['status']
        event(stage='master_metadata_projection', stream=stream,
              action=report['status'], coverage=report)
        return report
