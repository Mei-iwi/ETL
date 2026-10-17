from data_sync_etl.ports.repositories import UnitOfWork


class ListResourceLookups:
    """USED_ONLY_MASTER catalogs; canonical identity/count/page stay in SQL."""
    def __init__(self, uow: UnitOfWork):
        self.uow = uow

    def _page(self, kind, page, size, query):
        with self.uow() as repo:
            return repo.catalog_page(kind, page=page, size=size, query=query)

    def list_resource_types(self, *, page: int, size: int, query: str | None) -> dict:
        return self._page("types", page, size, query)

    def list_providers(self, *, page: int, size: int, query: str | None) -> dict:
        return self._page("providers", page, size, query)

    def list_grade_levels(self, *, page: int, size: int, query: str | None) -> dict:
        return self._page("grades", page, size, query)
