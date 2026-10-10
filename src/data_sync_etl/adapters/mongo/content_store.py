from typing import Any

from pymongo.errors import DuplicateKeyError

from data_sync_etl.domain.core import DomainError


class MongoContentStore:
    def __init__(self, database) -> None:
        self.database = database

    def _upsert(self, collection_name: str, document: dict[str, Any]) -> None:
        document_id = document["_id"]

        values = {key: value for key, value in document.items() if key not in ("_id", "created_at")}

        generation = document.get("projection_generation")
        selector: dict[str, Any] = {"_id": document_id}
        if generation is not None:
            selector["$or"] = [
                {"projection_generation": {"$lte": generation}},
                {"projection_generation": {"$exists": False}},
            ]
        try:
            self.database[collection_name].update_one(
                selector,
                {
                    "$set": values,
                    "$setOnInsert": {"created_at": document["created_at"]},
                },
                upsert=True,
            )
        except DuplicateKeyError:
            raise DomainError("Finalization ownership lost") from None

    def upsert_ocr_page(self, document: dict[str, Any]) -> None:
        self._upsert("ocr_pages", document)

    def upsert_content_unit(self, document: dict[str, Any]) -> None:
        self._upsert("mongo_content_units", document)

    def reconcile_content_units(
        self,
        version_id: str,
        unit_ids: list[str],
        generation: int | None = None,
    ) -> None:
        # Retain historical documents; only mark obsolete units after all upserts succeed.
        selector: dict[str, Any] = {
            "resource_version_id": version_id,
            "_id": {"$nin": unit_ids},
        }
        if generation is not None:
            selector["$or"] = [
                {"projection_generation": {"$lte": generation}},
                {"projection_generation": {"$exists": False}},
            ]
        self.database["mongo_content_units"].update_many(
            selector,
            {"$set": {"is_current": False}},
        )
