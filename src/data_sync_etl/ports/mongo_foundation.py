"""Future application seams; search candidates still require PostgreSQL authorization."""

from typing import Protocol

from data_sync_etl.domain.mongo_documents import (
    ContentBagDocument,
    ContentUnitDocument,
    OCRPageDocument,
    ResourceDescriptionDocument,
    UserProfileDocument,
)


class MetadataDocumentPort(Protocol):
    def sync_source_metadata(self, stream: str, document) -> str: ...
    def upsert_user(self, document: UserProfileDocument) -> None: ...
    def get_user(self, document_id: str) -> UserProfileDocument | None: ...
    def upsert_resource(self, document: ResourceDescriptionDocument) -> None: ...
    def get_resource(self, document_id: str) -> ResourceDescriptionDocument | None: ...
    def ensure_user_identity(self, document: UserProfileDocument) -> bool: ...
    def ensure_resource_identity(self, document: ResourceDescriptionDocument) -> bool: ...


class SourceMetadataPort(Protocol):
    def assert_distinct_destination(self) -> None: ...
    def get_user(self, document_id: str) -> UserProfileDocument | None: ...
    def get_resource(self, document_id: str) -> ResourceDescriptionDocument | None: ...


class ContentReadPort(Protocol):
    def ocr_pages(
        self,
        *,
        version_id: str | None = None,
        job_id: str | None = None,
        page_num: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[OCRPageDocument]: ...
    def get_unit(self, unit_id: str) -> ContentUnitDocument | None: ...
    def units(
        self,
        version_id: str,
        *,
        page_from: int | None = None,
        page_to: int | None = None,
        is_current: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ContentUnitDocument]: ...
    def keyword_candidates(
        self, keywords: list[str], *, version_id: str | None = None, limit: int = 100
    ) -> list[ContentUnitDocument]: ...


class KeywordExtractorPort(Protocol):
    def extract(self, text: str) -> list[str]: ...


class LeafKeywordDocumentPort(Protocol):
    def get_unit(self, unit_id: str) -> ContentUnitDocument | None: ...
    def update_leaf_keywords(self, document: ContentUnitDocument, keywords: list[str]) -> None: ...


class EmbeddingPort(Protocol):
    model_id: str
    version: str
    dimension: int

    def embed(self, text: str) -> list[float]: ...


class ContentBagPort(Protocol):
    def upsert_bag(self, document: ContentBagDocument) -> None: ...
    def get_bag(self, root_id: str) -> ContentBagDocument | None: ...
    def bag_candidates(
        self, keywords: list[str], *, version_id: str | None = None, limit: int = 100
    ) -> list[ContentBagDocument]: ...
