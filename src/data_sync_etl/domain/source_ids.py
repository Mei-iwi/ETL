import hashlib
import re
import unicodedata
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from data_sync_etl.domain.core import DomainError

SOURCE_PREFIXES = frozenset(
    {"ROL", "USR", "EDU", "GRD", "SUB", "PRV", "RTY", "RES", "RSV", "CUN", "OCR", "OPT"}
)


_IDENTITY_VERSION = 1
_IDENTITY_MAX_PART = 255


def _canonical_part(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = unicodedata.normalize("NFC", value)
    if not value or len(value) > _IDENTITY_MAX_PART or value != value.strip():
        raise ValueError(f"{field} must be a non-empty trimmed string of at most 255 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{field} contains a control character")
    if "://" in value or any(token in value.casefold() for token in ("password=", "secret=")):
        raise ValueError(f"{field} must be an opaque non-URI identifier")
    return value


class SourceLocator(BaseModel):
    """Opaque, non-secret identity of one record in a configured SOURCE."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: int = Field(default=_IDENTITY_VERSION, ge=1, le=255)
    backend: str = Field(min_length=1, max_length=64)
    namespace: str = Field(min_length=1, max_length=_IDENTITY_MAX_PART)
    entity_type: str = Field(min_length=1, max_length=64)
    source_record_id: str = Field(min_length=1, max_length=_IDENTITY_MAX_PART)

    @field_validator("backend", "namespace", "entity_type", "source_record_id")
    @classmethod
    def canonical_part(cls, value, info):
        return _canonical_part(value, info.field_name)

    def canonical_payload(self) -> tuple[str, ...]:
        # A tuple plus length-prefix encoding avoids delimiter ambiguity and
        # gives the hash an explicit domain/version separation.
        return (
            "ETL-SOURCE-IDENTITY",
            str(self.version),
            self.backend.casefold(),
            self.namespace,
            self.entity_type.casefold(),
            self.source_record_id,
        )


class SourceIdentity(BaseModel):
    """Stable digest plus its auditable locator; never contains a URI/secret."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    locator: SourceLocator
    digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_digest(self):
        if self.digest != source_identity_hash(self.locator):
            raise ValueError("Source identity digest does not match its locator")
        return self


def source_identity_hash(locator: SourceLocator) -> str:
    parts = locator.canonical_payload()
    payload = b"".join(
        len(part.encode("utf-8")).to_bytes(4, "big") + part.encode("utf-8")
        for part in parts
    )
    return hashlib.sha256(payload).hexdigest()


def source_identity(locator: SourceLocator) -> SourceIdentity:
    return SourceIdentity(locator=locator, digest=source_identity_hash(locator))


def validate_counter_key(prefix: str, period: str) -> None:
    if (
        not isinstance(prefix, str)
        or not isinstance(period, str)
        or prefix not in SOURCE_PREFIXES
        or not re.fullmatch(r"(?:0[1-9]|1[0-2])[0-9]{2}", period)
    ):
        raise DomainError("Invalid source ID prefix or MMYY period")


def source_period(created_at: datetime) -> str:
    if (
        not isinstance(created_at, datetime)
        or created_at.tzinfo is None
        or created_at.utcoffset() is None
    ):
        raise DomainError("Source ID requires an explicit timezone-aware creation time")
    return created_at.astimezone(UTC).strftime("%m%y")
