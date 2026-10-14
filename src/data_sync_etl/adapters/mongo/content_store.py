from typing import Any

from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.mongo_documents import ContentUnitDocument, Document, OCRPageDocument


def generation_filter(generation: int | None) -> dict:
    if generation is None:
        return {"projection_generation": {"$exists": False}}
    return {
        "$or": [
            {"projection_generation": {"$lte": generation}},
            {"projection_generation": {"$exists": False}},
        ]
    }


def invalidate_bags(database, version_id: str, generation: int | None) -> None:
    database["content_bags"].update_many(
        {"resource_version_id": version_id, **generation_filter(generation)},
        {"$set": {"is_current": False, "updated_at": now()}},
    )


class MongoContentStore:
    def __init__(self, database, *, invalidate_content_bags=True) -> None:
        self.database = database
        self.invalidate_content_bags = invalidate_content_bags

    def _upsert(self, collection_name: str, document: dict[str, Any]) -> None:
        document_id = document["_id"]

        values = {key: value for key, value in document.items() if key not in ("_id", "created_at")}

        generation = document.get("projection_generation")
        selector: dict[str, Any] = {"_id": document_id}
        selector.update(generation_filter(generation))
        # Pipeline expressions preserve created_at, and preserve extracted keywords only
        # for identical text. $literal prevents content beginning with '$' being evaluated.
        update = {key: {"$literal": value} for key, value in values.items()}
        update["created_at"] = {"$ifNull": ["$created_at", {"$literal": document["created_at"]}]}
        if collection_name == "mongo_content_units":
            update["keywords"] = {
                "$cond": [
                    {"$eq": ["$text", {"$literal": document["text"]}]},
                    {"$ifNull": ["$keywords", {"$literal": document.get("keywords")}]},
                    {"$literal": document.get("keywords")},
                ]
            }
            # Conservative rebuild: this may defer indexing after unchanged retries,
            # but can never advertise bags derived from partially replaced content.
            if self.invalidate_content_bags:
                invalidate_bags(self.database, document["resource_version_id"], generation)
        try:
            self.database[collection_name].update_one(
                selector,
                [{"$set": update}],
                upsert=True,
            )
        except DuplicateKeyError:
            raise DomainError("Finalization ownership lost") from None

    def upsert_ocr_page(self, document: dict[str, Any]) -> None:
        self._upsert("ocr_pages", self._validate(OCRPageDocument, document))

    def upsert_content_unit(self, document: dict[str, Any]) -> None:
        self._upsert("mongo_content_units", self._validate(ContentUnitDocument, document))

    @staticmethod
    def _validate(model, document):
        try:
            if isinstance(document, Document):
                document = document.bson()
            return model.model_validate(document).bson()
        except ValidationError:
            raise DomainError("Invalid Mongo content document") from None

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
        selector.update(generation_filter(generation))
        if self.invalidate_content_bags:
            invalidate_bags(self.database, version_id, generation)
        self.database["mongo_content_units"].update_many(
            selector,
            {"$set": {"is_current": False, "updated_at": now()}},
        )
