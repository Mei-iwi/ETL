"""Bounded read-only search seams; no schema or persistence for transient chunks."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol

from data_sync_etl.domain.mongo_documents import ContentBagDocument

SEARCH_DOCUMENT_VALIDATION = ContextVar('search_document_validation', default=None)


@contextmanager
def document_validation_scope():
    """Allow exact-document validation reuse only during one search call."""
    token = SEARCH_DOCUMENT_VALIDATION.set({'documents': {}, 'bytes': 0})
    try:
        yield
    finally:
        SEARCH_DOCUMENT_VALIDATION.reset(token)


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
    def semantic_metadata(self, resource_id: str) -> dict: ...


class SearchTextReader(Protocol):
    def search_unit_texts(self, unit_ids: list[str]) -> dict[str, dict]: ...
