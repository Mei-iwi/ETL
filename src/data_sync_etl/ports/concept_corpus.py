"""Read capabilities only. No generic client, command, write, media or model port."""
from dataclasses import dataclass, field
from typing import Iterable, Protocol


class ConceptBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class ResourceEvidence:
    resource_id: str
    version_id: str
    revision: int
    domain: str
    fields: dict[str, str]
    units: tuple[dict, ...] = ()
    public: bool = False
    active: bool = True
    deleted: bool = False
    published: bool = True
    ready: bool = False
    lifecycle: str = 'APPROVED'
    metadata_approved: bool = False
    scope: str = 'unknown'
    source_updated_at: float = 0.
    sense: str = ''
    approved_unit_ids: frozenset[str] = field(default_factory=frozenset)
    mongo_id: str | None = None
    projection_generation: int | None = None
    authority_fingerprint: str = ''


class ResourceEnumerator(Protocol):
    def page(self, after: str, limit: int) -> list[ResourceEvidence]: ...


class EvidenceReader(Protocol):
    def fields(self, resource: ResourceEvidence) -> Iterable[tuple[str, str, str]]: ...


class EligibilityReader(Protocol):
    completeness_verified: bool
    scope: str
    def token(self) -> str: ...
    def current(self, resource_id: str) -> ResourceEvidence | None: ...


class ChangeInventory(ResourceEnumerator, EligibilityReader, Protocol):
    """Cursor is an inventory key; periodic rewind is required without complete CDC."""


class RevocationAuthorityPort(EligibilityReader, Protocol):
    """Trusted provider must cover every mutable source, never a role/env flag.

    attestation returns domain.concept_authority.AuthorityAttestation. No live
    implementation is installed until the external operator verifies its feed.
    """
    def attestation(self): ...


class ReviewIdentityPort(Protocol):
    def verify(self, reviewer: str, statement: str, proof=None, *, context=None) -> dict: ...


class ConceptResolver(Protocol):
    def pin(self, query: str, domain: str | None = None): ...
