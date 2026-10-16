"""Bounded shared read-only validation, or explicitly configured separate-source copy."""

from data_sync_etl.application.master_metadata_projection import useful_extension
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.logging import event

MODELS = {'users': 'master_users', 'resources': 'master_learning_resources'}


class SourceMongoMetadataSync:
    def __init__(self, uow, source, destination, *, reference_guard=None, subject_guard=None, batch_size=100, shared=False):
        if type(batch_size) is not int or not 1 <= batch_size <= 1000:
            raise ValueError('Invalid metadata batch size')
        self.uow, self.source, self.destination = uow, source, destination
        self.reference_guard, self.subject_guard, self.batch_size = reference_guard, subject_guard, batch_size
        if shared and destination is not None:
            raise ValueError('Shared metadata validation does not accept a writer')
        self.shared = shared

    @staticmethod
    def useful_extension(document):
        return useful_extension(document)

    def run(self, stream='all', *, dry_run=False):
        if stream != 'all' and stream not in MODELS:
            raise DomainError('Unknown metadata stream')
        return {name: self._stream(name, dry_run=dry_run) for name in (MODELS if stream == 'all' else [stream])}

    def _stream(self, stream, *, dry_run=False):
        report = dict(scanned=0, inserted=0, updated=0, unchanged=0, skipped_unmapped=0,
                      missing_extension=0, skipped_ineligible=0, skipped_stale=0, conflicts=0, failed=0,
                      mode='FULL_RECONCILE', incremental_available=False, checkpoint=None,
                      checkpoint_persisted=False, error_code=None, master_state='COMMITTED',
                      eligible=0, would_sync=0, dry_run=dry_run, last_run=now().isoformat(), synced_fields=[])
        report['conflict_reasons'] = {}
        report['master_driven_scan'] = True
        if self.shared:
            report.update(mode='VALIDATE_SHARED', matched=0, missing_mapping=0, missing_document=0, invalid_document=0)
        if self.source is None or (not self.shared and self.destination is None):
            report.update(status='DISABLED', error_code='SHARED_MONGO_UNAVAILABLE' if self.shared else 'SOURCE_MONGO_NOT_CONFIGURED')
            event(stage='mongo_metadata_validation' if self.shared else 'mongo_source_metadata_sync', stream=stream, action='DISABLED', coverage=report)
            return report
        with self.uow.metadata_projection_lock(stream) as acquired:
            if not acquired:
                return {**report, 'status': 'BUSY'}
            operation = 'SOURCE_MONGO_CONFIGURATION_FAILED'
            try:
                if not self.shared:
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
                        if (row['source_origin'] != 'POSTGRESQL' or not row['is_active'] or row['deleted_at']
                                or row.get('publication_status') == 'ARCHIVED'):
                            report['skipped_ineligible'] += 1
                            continue
                        report['eligible'] += 1
                        reference = references.get(row['id'])
                        # Verified SOURCE NULL + reserved Master PK is the existing,
                        # opt-in identity fallback; never accept an unverified/non-PK fallback.
                        if (self.shared and row['id'] in references and reference is None
                                and row['mongo_id'] == row['id']):
                            reference = row['id']
                        if reference is None:
                            report['skipped_unmapped'] += 1
                            if self.shared:
                                report['missing_mapping'] += 1
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
                            operation = 'SHARED_MONGO_READ_FAILED' if self.shared else 'SOURCE_MONGO_READ_FAILED'
                            document = (self.source.get_user(reference) if stream == 'users'
                                        else self.source.get_resource(reference))
                            if self.shared and document is None:
                                report['missing_document'] += 1
                                continue
                            if document is None or not self.useful_extension(document):
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
                            if (current is None or current['source_origin'] != 'POSTGRESQL'
                                    or current['mongo_id'] != reference or not current['is_active']
                                    or current['deleted_at'] or current.get('publication_status') == 'ARCHIVED'):
                                raise DomainError('MONGO_METADATA_MAPPING_CHANGED')
                            if self.shared:
                                report['matched'] += 1
                                continue
                            if dry_run:
                                report['would_sync'] += 1
                                continue
                            operation = 'TARGET_MONGO_WRITE_FAILED'
                            result = self.destination.sync_source_metadata(stream, document)
                            counter = {'INSERTED': 'inserted', 'UPDATED': 'updated', 'UNCHANGED': 'unchanged', 'STALE': 'skipped_stale'}[result]
                            report[counter] += 1
                            if result in ('INSERTED', 'UPDATED'):
                                report['synced_fields'] = sorted(set(report['synced_fields']) |
                                    (document.model_fields_set - {'id', 'created_at', 'updated_at'}))
                        except DomainError as exc:
                            code = str(exc) if str(exc) in {
                                'MONGO_METADATA_ID_MISMATCH', 'MONGO_METADATA_SUBJECT_MISMATCH',
                                'MONGO_METADATA_MAPPING_CHANGED', 'MONGO_METADATA_CONCURRENT_CHANGE',
                                'BLOCKED_MONGO_ID_MAPPING',
                            } else 'MONGO_METADATA_DOCUMENT_INVALID'
                            report['invalid_document' if self.shared and code == 'MONGO_METADATA_DOCUMENT_INVALID' else 'conflicts'] += 1
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
                if self.shared:
                    partial = partial or bool(report['missing_document'] or report['invalid_document'])
                report['status'] = 'PARTIAL' if partial else 'SHARED_MONGO_VALIDATED' if self.shared else 'DRY_RUN' if dry_run else 'COMPLETED'
        event(stage='mongo_metadata_validation' if self.shared else 'mongo_source_metadata_sync', stream=stream, action=report['status'], coverage=report)
        return report
