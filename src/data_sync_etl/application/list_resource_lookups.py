from data_sync_etl.domain.core import DomainError
from data_sync_etl.ports.repositories import UnitOfWork
from data_sync_etl.ports.source_catalogs import ReadOnlySourceCatalogPort, SourceCatalogError


class ListResourceLookups:
    """Read SOURCE catalogs plus valid LOCAL identities; explicit partial fallback."""
    def __init__(self, uow: UnitOfWork, source: ReadOnlySourceCatalogPort | None = None):
        self.uow, self.source = uow, source

    def _page(self, kind, page, size, query):
        if page < 1 or not 1 <= size <= 100:
            raise ValueError("Invalid catalog pagination")
        rows, status = None, "SOURCE_DISABLED"
        if kind in {"providers", "types"} and self.source is not None:
            try:
                rows = self.source.catalog_rows(kind)
                status = "SOURCE_OK" if any(r["available"] for r in rows) else "SOURCE_EMPTY"
            except SourceCatalogError as exc:
                status = exc.status
                if status != "SOURCE_UNAVAILABLE":
                    raise
        with self.uow() as repo:
            kwargs = {"source_rows": rows} if rows is not None else {}
            result = repo.catalog_page(kind, page=page, size=size, query=query, **kwargs)
        result["catalog"] = {
            "identity_overlaps": result.pop("identity_overlaps", 0),
            "source_status": status if kind != "grades" else "MASTER_MANAGED",
            "completeness": ("ALL_SOURCE_PLUS_LOCAL" if rows is not None else
                             "MASTER_MANAGED" if kind == "grades" else "PARTIAL_FALLBACK"),
        }
        return result

    def source_selection(self, kind, identity):
        """Resolve an existing SOURCE identity for authorized LOCAL form writes.

        This reads SOURCE only. Unavailable SOURCE keeps legacy Master validation;
        inactive identities cannot be resurrected by submitting stale form fields.
        """
        if self.source is None:
            return None
        try:
            rows = self.source.catalog_rows(kind)
        except SourceCatalogError as exc:
            if exc.status == "SOURCE_UNAVAILABLE":
                return None
            raise
        key = "id" if kind == "providers" else "code"
        selected = next((row for row in rows if row[key] == identity), None)
        if selected is not None and not selected["available"]:
            raise DomainError("Selected SOURCE catalog entry is inactive")
        return selected

    def list_resource_types(self, *, page: int, size: int, query: str | None) -> dict:
        return self._page("types", page, size, query)

    def list_providers(self, *, page: int, size: int, query: str | None) -> dict:
        return self._page("providers", page, size, query)

    def list_grade_levels(self, *, page: int, size: int, query: str | None) -> dict:
        return self._page("grades", page, size, query)
