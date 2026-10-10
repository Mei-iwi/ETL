from data_sync_etl.domain.core import ResourceNotFound
from data_sync_etl.ports.repositories import UnitOfWork


class GetResourceDetail:
    def __init__(self, uow: UnitOfWork):
        self.uow = uow

    def run(self, resource_id: str) -> dict:
        with self.uow() as repo:
            resource = repo.get_resource_detail(resource_id)
            if not resource:
                raise ResourceNotFound(f"Resource {resource_id} not found")
            return resource
