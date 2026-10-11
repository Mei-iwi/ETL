from data_sync_etl.domain.core import ResourceNotFound
from data_sync_etl.ports.repositories import UnitOfWork


class ListResourceVersions:
    def __init__(self, uow: UnitOfWork):
        self.uow = uow

    def run(self, resource_id: str) -> list[dict]:
        with self.uow() as repo:
            if repo.get("master_learning_resources", resource_id) is None:
                raise ResourceNotFound("Resource not found")

            versions = repo.find(
                "resource_versions",
                {"resource_id": resource_id},
                order=("-version_number", "-created_at"),
            )

        # Chỉ trả metadata phiên bản; không trả đường dẫn lưu trữ hoặc source_hash.
        return [
            {
                "id": version["id"],
                "resource_id": version["resource_id"],
                "parent_version_id": version["parent_version_id"],
                "version_number": version["version_number"],
                "lifecycle_status": version["lifecycle_status"],
                "uploaded_by": version["uploaded_by"],
                "created_at": version["created_at"],
                "updated_at": version["updated_at"],
            }
            for version in versions
        ]