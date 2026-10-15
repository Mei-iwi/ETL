"""Five storage contracts; normalization is not keyword extraction or authorization."""

import math
import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DocumentId = Annotated[str, Field(strict=True, min_length=1, max_length=30)]
Int32 = Annotated[int, Field(strict=True, ge=1, le=2**31 - 1)]
STRUCTURED_BAG_TYPES = ('CHAPTER', 'LESSON', 'SECTION')
# Actual native source_type values emitted by the existing Postprocessor; no media types.
FALLBACK_BAG_TYPES = ('PAGE_TEXT', 'TXT_TEXT', 'CSV_TEXT', 'DOCX_TEXT', 'XLSX_TEXT', 'PPTX_TEXT', 'IMAGE_TEXT')
BagUnitType = Literal['CHAPTER', 'LESSON', 'SECTION', 'PAGE_TEXT', 'TXT_TEXT', 'CSV_TEXT',
                      'DOCX_TEXT', 'XLSX_TEXT', 'PPTX_TEXT', 'IMAGE_TEXT']


def normalize_keywords(values: list[str]) -> list[str]:
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise ValueError("Keywords must be an array of strings")
    return sorted({unicodedata.normalize("NFC", v).strip().casefold() for v in values} - {""})


class Document(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        strict=True,
        hide_input_in_errors=True,
    )
    id: DocumentId = Field(alias="_id")
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_dates(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timezone-aware BSON dates required")
        return value.astimezone(UTC)

    def bson(self) -> dict:
        return self.model_dump(by_alias=True, exclude_unset=True)


class UserProfileDocument(Document):
    subject_id: DocumentId | None = None
    organization_name: Annotated[str, Field(max_length=255)] | None = None
    address: Annotated[str, Field(max_length=1000)] | None = None
    bio: Annotated[str, Field(max_length=2000)] | None = None


class Author(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    name: Annotated[str, Field(min_length=1)]
    organization: str | None = None
    role: str | None = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Author name required")
        return value


class ResourceDescriptionDocument(Document):
    description: str | None = None
    authors: list[Author] | None = None
    tags: list[str] | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def tags_normalized(cls, value):
        # Same explicit NFC/casefold/trim policy as keywords; not automatic extraction.
        return None if value is None else normalize_keywords(value)


class ProjectedDocument(Document):
    resource_version_id: DocumentId
    projection_generation: Int32 | None = None

    @model_validator(mode="before")
    @classmethod
    def generation_not_null(cls, value):
        explicitly_null = (
            isinstance(value, dict)
            and "projection_generation" in value
            and value["projection_generation"] is None
        ) or (
            isinstance(value, ProjectedDocument)
            and "projection_generation" in value.model_fields_set
            and value.projection_generation is None
        )
        if explicitly_null:
            raise ValueError("Omit absent generation; explicit null is not permitted")
        return value


class OCRPageDocument(ProjectedDocument):
    # Composite ID can be longer than a PostgreSQL identifier.
    id: Annotated[str, Field(strict=True, min_length=1, max_length=41)] = Field(alias="_id")
    job_id: DocumentId
    page_num: Int32
    raw_text: str
    char_count: Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]
    source_checksum: Annotated[str, Field(min_length=1)]
    confidence: Annotated[float, Field(allow_inf_nan=False)] | None = None
    engine_name: str | None = None
    engine_version: str | None = None

    @model_validator(mode="after")
    def page_consistent(self) -> Self:
        if self.id != f"{self.job_id}:{self.page_num}" or self.char_count != len(self.raw_text):
            raise ValueError("OCR identity or Unicode codepoint count inconsistent")
        return self


class ContentUnitDocument(ProjectedDocument):
    title: Annotated[str, Field(max_length=500)] | None = None
    page_from: Int32 | None = None
    page_to: Int32 | None = None
    text: str
    markdown: str
    keywords: Annotated[list[str], Field(max_length=5)] | None = None
    content_unit_id: DocumentId | None = None
    page_result_id: DocumentId | None = None
    is_current: bool = True

    @field_validator("keywords", mode="before")
    @classmethod
    def keywords_normalized(cls, value):
        return None if value is None else normalize_keywords(value)

    @model_validator(mode="after")
    def unit_consistent(self) -> Self:
        if (
            self.page_from is not None
            and self.page_to is not None
            and self.page_to < self.page_from
        ):
            raise ValueError("Invalid page range")
        if self.content_unit_id is not None and self.content_unit_id != self.id:
            raise ValueError("Content unit ID mismatch")
        return self


class ContentBagDocument(ProjectedDocument):
    unit_type: BagUnitType
    title: Annotated[str, Field(max_length=500)] | None = None
    keywords: list[str]
    embedding_text: str
    embedding: list[float]
    embedding_model: Annotated[str, Field(min_length=1, max_length=255)]
    is_current: bool = True
    # Snapshot of current leaf texts/keywords/generations, not a search-ready flag.
    content_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None

    @field_validator("keywords", mode="before")
    @classmethod
    def keywords_normalized(cls, value):
        return normalize_keywords(value)

    @model_validator(mode="after")
    def vector_consistent(self) -> Self:
        if not self.keywords or self.embedding_text != " | ".join(self.keywords):
            raise ValueError("Embedding input inconsistent")
        if (
            not self.embedding
            or not all(math.isfinite(v) for v in self.embedding)
            or not any(self.embedding)
            or not self.embedding_model.strip()
        ):
            raise ValueError("Finite nonzero embedding and model identifier required")
        return self


DOCUMENT_MODELS = {
    "users": UserProfileDocument,
    "learning_resources": ResourceDescriptionDocument,
    "ocr_pages": OCRPageDocument,
    "mongo_content_units": ContentUnitDocument,
    "content_bags": ContentBagDocument,
}
