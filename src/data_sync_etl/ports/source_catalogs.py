"""Private read-only SOURCE catalog contract, no ORM/schema changes."""
from typing import Protocol


class SourceCatalogError(Exception):
    def __init__(self, status: str):
        self.status = status
        super().__init__(status)


class ReadOnlySourceCatalogPort(Protocol):
    def catalog_rows(self, kind: str) -> list[dict]: ...
