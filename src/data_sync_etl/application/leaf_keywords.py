"""Save explicitly supplied keywords, with PostgreSQL leaf-membership validation."""

from data_sync_etl.domain.core import DomainError
from data_sync_etl.ports.mongo_foundation import LeafKeywordDocumentPort


class SaveLeafKeywords:
    def __init__(self, uow, documents: LeafKeywordDocumentPort):
        self.uow = uow
        self.documents = documents

    def execute(self, unit_id: str, keywords: list[str]) -> None:
        with self.uow() as repo:
            unit = repo.get("content_units", unit_id)
            if unit is None or repo.find("content_units", {"parent_unit_id": unit_id}, limit=1):
                raise DomainError("Keywords require a PostgreSQL leaf ContentUnit")
            observed = self.documents.get_unit(unit_id)
            if observed is None or observed.resource_version_id != unit["resource_version_id"]:
                raise DomainError("Leaf projection unavailable or version mismatch")
            self.documents.update_leaf_keywords(observed, keywords)
