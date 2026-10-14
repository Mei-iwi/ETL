"""Bounded full reconciliation of source-owned extensions; never synthesizes identities."""

from data_sync_etl.domain.core import DomainError
from data_sync_etl.logging import event

MODELS = {'users': 'master_users', 'resources': 'master_learning_resources'}


class SourceMongoMetadataSync:
    def __init__(self, uow, source, destination, *, reference_guard=None, subject_guard=None, batch_size=100):
        if type(batch_size) is not int or not 1 <= batch_size <= 1000:
            raise ValueError('Invalid metadata batch size')
        self.uow, self.source, self.destination = uow, source, destination
        self.reference_guard, self.subject_guard, self.batch_size = reference_guard, subject_guard, batch_size

    def run(self, stream='all'):
        if stream != 'all' and stream not in MODELS:
            raise DomainError('Unknown metadata stream')
        return {name: self._stream(name) for name in (MODELS if stream == 'all' else [stream])}

    def _stream(self, stream):
        report = dict(scanned=0, inserted=0, updated=0, unchanged=0, skipped_unmapped=0,
                      missing_extension=0, skipped_ineligible=0, skipped_stale=0, conflicts=0, failed=0,
                      mode='FULL_RECONCILE', incremental_available=False, checkpoint=None,
                      checkpoint_persisted=False, error_code=None, master_state='COMMITTED')
        report['conflict_reasons'] = {}
        report['master_driven_scan'] = True
        if self.source is None or self.destination is None:
            report.update(status='DISABLED', error_code='SOURCE_MONGO_NOT_CONFIGURED')
            event(stage='mongo_source_metadata_sync', stream=stream, action='DISABLED', coverage=report)
            return report
        with self.uow.metadata_projection_lock(stream) as acquired:
            if not acquired:
                return {**report, 'status': 'BUSY'}
            operation = 'SOURCE_MONGO_CONFIGURATION_FAILED'
            try:
                self.source.assert_distinct_destination()
                if self.reference_guard is None:
                    raise DomainError('SOURCE_PG_VERIFIER_REQUIRED')
                after = None
                while True:
                    with self.uow() as repo:
                        rows = repo.source_metadata_page(stream, after, self.batch_size)
                    if not rows:
                        break
                    operation = 'SOURCE_PG_VERIFICATION_FAILED'
                    references = self.reference_guard(stream, [row['id'] for row in rows])
                    if not isinstance(references, dict):
                        raise DomainError('SOURCE_PG_VERIFIER_INVALID')
                    for row in rows:
                        report['scanned'] += 1
                        reference = references.get(row['id'])
                        if reference is None:
                            report['skipped_unmapped'] += 1
                            continue
                        if (not isinstance(reference, str) or not 1 <= len(reference) <= 30
                                or reference != reference.strip() or reference != row['mongo_id']):
                            report['conflicts'] += 1
                            reasons = report['conflict_reasons']
                            reasons['MONGO_METADATA_ID_MISMATCH'] = reasons.get('MONGO_METADATA_ID_MISMATCH', 0) + 1
                            continue
                        if not row['is_active'] or row['deleted_at'] or row.get('publication_status') == 'ARCHIVED':
                            report['skipped_ineligible'] += 1
                            continue
                        try:
                            operation = 'SOURCE_MONGO_READ_FAILED'
                            document = (self.source.get_user(reference) if stream == 'users'
                                        else self.source.get_resource(reference))
                            if document is None or not (document.model_fields_set - {'id', 'created_at', 'updated_at'}):
                                report['missing_extension'] += 1
                                continue
                            if document.id != reference:
                                raise DomainError('MONGO_METADATA_ID_MISMATCH')
                            if stream == 'users' and document.subject_id is not None:
                                operation = 'SOURCE_PG_VERIFICATION_FAILED'
                                with self.uow() as repo:
                                    valid_subject = repo.metadata_subject_exists(document.subject_id)
                                if not valid_subject or self.subject_guard is None or not self.subject_guard(document.subject_id):
                                    raise DomainError('MONGO_METADATA_SUBJECT_MISMATCH')
                            # Recheck the mapping after network reads, with no PG transaction held for RPC.
                            with self.uow() as repo:
                                current = repo.metadata_sync_state(stream, row['id'])
                            if (current is None or current['mongo_id'] != reference or not current['is_active']
                                    or current['deleted_at'] or current.get('publication_status') == 'ARCHIVED'):
                                raise DomainError('MONGO_METADATA_MAPPING_CHANGED')
                            operation = 'TARGET_MONGO_WRITE_FAILED'
                            result = self.destination.sync_source_metadata(stream, document)
                            counter = {'INSERTED': 'inserted', 'UPDATED': 'updated', 'UNCHANGED': 'unchanged', 'STALE': 'skipped_stale'}[result]
                            report[counter] += 1
                        except DomainError as exc:
                            report['conflicts'] += 1
                            code = str(exc) if str(exc) in {
                                'MONGO_METADATA_ID_MISMATCH', 'MONGO_METADATA_SUBJECT_MISMATCH',
                                'MONGO_METADATA_MAPPING_CHANGED', 'MONGO_METADATA_CONCURRENT_CHANGE',
                                'BLOCKED_MONGO_ID_MAPPING',
                            } else 'MONGO_METADATA_DOCUMENT_INVALID'
                            report['conflict_reasons'][code] = report['conflict_reasons'].get(code, 0) + 1
                        except Exception:
                            report['failed'] += 1
                            raise
                    # Diagnostic only, not a persisted incremental checkpoint. Retry starts a full scan.
                    after = rows[-1]['id']
                    report['checkpoint'] = after
            except DomainError as exc:
                code = str(exc) if str(exc) in {
                    'SOURCE_MONGO_SAME_DESTINATION', 'SOURCE_PG_VERIFIER_REQUIRED', 'SOURCE_PG_VERIFIER_INVALID',
                } else 'SOURCE_MONGO_CONFIGURATION_OR_MAPPING'
                report.update(status='BLOCKED', error_code=code)
            except Exception:
                report.update(status='FAILED', error_code=operation)
            else:
                partial = any(report[key] for key in ('missing_extension', 'conflicts', 'skipped_unmapped', 'skipped_stale', 'skipped_ineligible'))
                report['status'] = 'PARTIAL' if partial else 'COMPLETED'
        event(stage='mongo_source_metadata_sync', stream=stream, action=report['status'], coverage=report)
        return report
