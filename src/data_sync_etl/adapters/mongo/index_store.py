"""Index artifacts only. Never mutates OCR, metadata, content units or bags."""

from data_sync_etl.domain.indexing import IndexingError


class MongoIndexStore:
    def __init__(self, database):
        self.database = database

    def ensure_ready(self):
        from data_sync_etl.adapters.mongo.schema import INDEXES, compatible_index, contracts

        entry = next(self.database.list_collections(filter={"name": "indexed_chunks"}), None)
        if (not entry or entry.get("options", {}).get("validator") != contracts(256)["indexed_chunks"]
                or entry.get("options", {}).get("validationAction", "error") != "error"
                or entry.get("options", {}).get("validationLevel", "strict") != "strict"):
            raise IndexingError("BLOCKED_INDEX_SCHEMA", 503)
        installed = list(self.database.indexed_chunks.list_indexes())
        if not all(any(compatible_index(row, expected) for row in installed)
                   for expected in INDEXES["indexed_chunks"]):
            raise IndexingError("BLOCKED_INDEX_SCHEMA", 503)

    def source_units(self, version_id, limit):
        return list(self.database.mongo_content_units.find(
            {"resource_version_id": version_id, "is_current": True},
            {"_id": 1, "content_unit_id": 1, "resource_version_id": 1,
             "text": 1, "page_from": 1, "page_to": 1, "projection_generation": 1},
        ).limit(limit))

    def upsert(self, document):
        self.database.indexed_chunks.replace_one({"_id": document["_id"]}, document, upsert=True)

    def artifacts(self, version_id, run_id, limit, offset=0):
        return list(self.database.indexed_chunks.find(
            {"resource_version_id": version_id, "index_run_id": run_id}
        ).sort("chunk_sequence", 1).skip(offset).limit(limit))
