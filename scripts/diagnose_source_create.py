"""Inspect an existing CREATE checkpoint; optional SOURCE version check rolls back."""

import argparse
import re
from contextlib import contextmanager
from datetime import datetime

from data_sync_etl.composition import Container
from data_sync_etl.domain.source_ids import SourceLocator
from data_sync_etl.domain.source_ownership import state_hash


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--rollback-version-check', action='store_true')
    parser.add_argument('--rollback-metadata-check', action='store_true')
    parser.add_argument('--recover-existing', action='store_true',
                        help='Explicitly retry only this job with its existing IDs and checkpoint')
    args = parser.parse_args()
    container = Container()
    try:
        with container.uow() as repo:
            job = repo.get('reconciliation_audit_jobs', args.job_id)
        if not job or job['audit_type'] != 'SOURCE_CREATE':
            raise ValueError('SOURCE_CREATE_JOB_REQUIRED')
        checkpoint = job['checkpoint']
        print({'status': job['status'], 'error_code': job.get('error_code'),
               'workflow': checkpoint.get('workflow')}, flush=True)
        if args.recover_existing:
            service = container.source_resource_create
            response = service.request(checkpoint['payload'],
                                       idempotency_key=checkpoint['idempotency_key'],
                                       actor_id=checkpoint['requested_by'], role=checkpoint['requested_role'],
                                       source_file=checkpoint.get('source_file'))
            service.process_pending_once('controlled-create-recovery', request_id=response['request_id'])
            if service.last_source_committed:
                container._reconcile_source_create()
                service.mark_master_reconciled()
            result = service.status_for_request(args.job_id)
            print({key: result.get(key) for key in ('status', 'error_code', 'workflow',
                   'source_resource_id', 'source_version_id', 'master_resource_id',
                   'master_version_id', 'master_reconciliation_status')}, flush=True)
            return
        if not args.rollback_version_check and not args.rollback_metadata_check:
            return
        writer = (container.source_metadata_writer if args.rollback_metadata_check
                  else container.source_version_writer)
        try:
            container.source_object_storage_writer.verify(
                bucket=checkpoint['source_bucket'], object_key=checkpoint['source_object_key'],
                size=checkpoint['source_file_size'], source_hash=checkpoint['source_hash'])
            print({'object_check': 'PASS'}, flush=True)
        except Exception as exc:
            print({'object_check': 'FAILED', 'exception': type(exc).__name__,
                   'code': str(exc) if re.fullmatch('[A-Z][A-Z0-9_]{2,80}', str(exc)) else None}, flush=True)
            return
        engine = writer.engine
        with engine.connect() as connection:
            transaction = connection.begin()

            @contextmanager
            def rollback_scope():
                yield connection

            # The adapter owns neither this outer transaction nor its commit.
            # Its normal CREATE executes against the exact contract/schema,
            # and every statement is unconditionally rolled back below.
            class BoundEngine:
                begin = staticmethod(rollback_scope)

            writer.engine = BoundEngine()
            try:
                if args.rollback_metadata_check:
                    source_id = checkpoint['source_resource_id']
                    before = writer._canonical_row(connection, source_id, lock=False)
                    result = writer.write_metadata(
                        locator=SourceLocator(backend='postgresql', namespace=writer.namespace,
                                              entity_type='resources', source_record_id=source_id),
                        fields={'title': before['title'][:470] + ' [rollback probe]',
                                'publication_status': before['publication_status'],
                                'subject_id': None, 'grade_level_id': None},
                        expected_source_state_hash=state_hash(writer._canonical_state(before)),
                        idempotency_key='rollback-metadata-check')
                    print({'metadata_check': result.get('status'), 'rollback': True}, flush=True)
                    return
                result = writer.create_version(
                    source_version_id=checkpoint['source_version_id'],
                    source_resource_id=checkpoint['source_resource_id'],
                    parent_version_id=None, version_number=None,
                    lifecycle_status=writer.default_lifecycle_status,
                    storage_bucket=checkpoint['source_bucket'],
                    storage_object_key=checkpoint['source_object_key'],
                    source_hash=checkpoint['source_hash'],
                    uploaded_by=checkpoint['source_user_id'],
                    created_at=datetime.fromisoformat(checkpoint.get('version_reserved_at')
                                                      or checkpoint['reserved_at']),
                    idempotency_key=checkpoint['source_version_idempotency_key'])
                print({'version_check': result.get('status'), 'rollback': True}, flush=True)
            except Exception as exc:
                orig = getattr(exc, 'orig', None)
                diag = getattr(orig, 'diag', None)
                print({'version_check': 'FAILED', 'exception': type(exc).__name__,
                       'sqlstate': getattr(orig, 'sqlstate', None),
                       'constraint': getattr(diag, 'constraint_name', None),
                       'column': getattr(diag, 'column_name', None),
                       'code': str(exc) if orig is None and re.fullmatch('[A-Z][A-Z0-9_]{2,80}', str(exc)) else None}, flush=True)
            finally:
                transaction.rollback()
                writer.engine = engine
    finally:
        container.close()


if __name__ == '__main__':
    main()
