"""Standalone audit process: python -m data_sync_etl.workers.reconciliation [--once]."""
import argparse
import os

from data_sync_etl.adapters.persistence.audit_schedule import AuditSchedule
from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
from data_sync_etl.composition import Container
from data_sync_etl.config import Settings
from data_sync_etl.logging import event
from data_sync_etl.workers.lifecycle import tick, worker_lifecycle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    container = Container(Settings())
    audit = ScheduledAudit(container.reconciliation_engine, container.settings, getattr(container, 'sync', None))
    # The common lifecycle owns its heartbeat file. Never let an audit process
    # overwrite/unlink a heartbeat inherited from a Sync worker launch profile.
    previous_heartbeat = os.environ.pop('ETL_WORKER_HEARTBEAT_FILE', None)
    if container.settings.reconciliation_worker_heartbeat_file is not None:
        os.environ['ETL_WORKER_HEARTBEAT_FILE'] = str(container.settings.reconciliation_worker_heartbeat_file)
    try:
        with worker_lifecycle() as stop:
            while not stop.is_set():
                tick()
                try:
                    if hasattr(container, 'uow'):
                        AuditSchedule(container.uow, container.settings).tick()
                    audit.schedule()
                    worked = audit.once()
                except Exception:
                    worked = False
                    event(stage='reconciliation', action='AUDIT_UNAVAILABLE')
                if args.once:
                    break
                stop.wait(1 if worked else 10)
    finally:
        os.environ.pop('ETL_WORKER_HEARTBEAT_FILE', None)
        if previous_heartbeat is not None:
            os.environ['ETL_WORKER_HEARTBEAT_FILE'] = previous_heartbeat
        container.close()


if __name__ == '__main__':
    main()
