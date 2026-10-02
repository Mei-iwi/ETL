from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.logging import event, flow
from data_sync_etl.ports.processing import StoragePort
from data_sync_etl.ports.repositories import UnitOfWork


class ResourceIngestion:
    def __init__(self, uow: UnitOfWork, storage: StoragePort):
        self.uow, self.storage = uow, storage

    def run(self, resource_id, bucket, object_key, uploaded_by=None):
        with flow():
            with self.uow() as repo:
                self._validate(repo.get("master_learning_resources", resource_id))
            snapshot = self.storage.snapshot(bucket, object_key)
            with self.uow() as repo:
                self._validate(repo.get("master_learning_resources", resource_id, lock=True))
                previous = repo.find(
                    "resource_versions",
                    {"resource_id": resource_id},
                    order=("-version_number",),
                    limit=1,
                )
                latest = previous[0] if previous else None
                if latest and latest["source_hash"] == snapshot.source_hash:
                    return {"action": "NO_CHANGE", "version": latest}
                version = repo.insert(
                    "resource_versions",
                    dict(
                        resource_id=resource_id,
                        parent_version_id=latest["id"] if latest else None,
                        version_number=latest["version_number"] + 1 if latest else 1,
                        lifecycle_status="INGESTED",
                        storage_bucket=snapshot.bucket,
                        storage_object_key=snapshot.object_key,
                        source_hash=snapshot.source_hash,
                        uploaded_by=uploaded_by,
                        created_at=now(),
                        updated_at=now(),
                    ),
                )
            event(
                resource_id=resource_id,
                resource_version_id=version["id"],
                stage="ingest",
                action="CREATED",
            )
            return {"action": "CREATED", "version": version}

    @staticmethod
    def _validate(resource):
        if not resource:
            raise DomainError("Resource not found")
        if not resource["is_active"] or resource["deleted_at"] is not None:
            raise DomainError("Resource is inactive or deleted")
