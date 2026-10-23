"""Bounded read-only search seams; no schema or persistence for transient chunks."""

from typing import Protocol

from data_sync_etl.domain.mongo_documents import ContentBagDocument


class SearchBagReader(Protocol):
    # Read the complete bounded corpus, including nonhits and full lexical TF.
    # Never prefilter by display keywords; cap+1 lets Search reject truncation.
    def search_bags(self, root_ids: list[str], *, version_id: str,
                    generation: int) -> dict[str, ContentBagDocument | None]: ...
    def ensure_ready(self) -> None: ...
    def get_bag(self, root_id: str) -> ContentBagDocument | None: ...
    def search_candidates(self, version_ids: list[str], *, model: str, limit: int) -> list[ContentBagDocument]: ...


class SearchMetadataReader(Protocol):
    def search_versions(self, filters: dict, *, limit: int) -> list[dict]: ...


class SearchTextReader(Protocol):
    def search_unit_texts(self, unit_ids: list[str]) -> dict[str, dict]: ...
