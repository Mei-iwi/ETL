"""Bounded read-only search seams; no schema or persistence for transient chunks."""

from typing import Protocol

from data_sync_etl.domain.mongo_documents import ContentBagDocument


class SearchBagReader(Protocol):
    def ensure_ready(self) -> None: ...
    def get_bag(self, root_id: str) -> ContentBagDocument | None: ...
    def search_candidates(self, version_ids: list[str], *, model: str, limit: int) -> list[ContentBagDocument]: ...


class SearchMetadataReader(Protocol):
    def search_versions(self, filters: dict, *, limit: int) -> list[dict]: ...
