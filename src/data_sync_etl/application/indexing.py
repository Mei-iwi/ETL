"""Version-scoped durable queue, builder and published-generation reader."""

from time import monotonic

from data_sync_etl.application.processing_state import processing_state
from data_sync_etl.domain.core import now
from data_sync_etl.domain.indexing import (
    IndexingError,
    bag_of_words,
    digest,
    validate_vector,
    word_windows,
)


class IndexingService:
    def __init__(self, uow, store, embedding, settings):
        self.uow, self.store, self.embedding, self.settings = uow, store, embedding, settings

    def _version(self, repo, version_id):
        version = repo.get("resource_versions", version_id)
        resource = repo.get("master_learning_resources", version["resource_id"]) if version else None
        if not resource or not resource["is_active"] or resource["deleted_at"] is not None:
            raise IndexingError("VERSION_NOT_FOUND", 404)
        jobs = repo.find("ocr_jobs", {"resource_version_id": version_id},
                         order=("-created_at", "-id"), limit=1)
        state = processing_state(repo, jobs[0]) if jobs else "NOT_STARTED"
        return version, state

    def _runs(self, repo, version_id):
        return repo.find("etl_stage_runs", {"resource_version_id": version_id, "stage": "INDEX_BUILD"},
                         order=("-created_at", "-id"))

    def _snapshot(self, repo, version_id):
        version, state = self._version(repo, version_id)
        if state != "CONTENT_READY":
            raise IndexingError("CONTENT_NOT_READY")
        if self.store is None:
            raise IndexingError("BLOCKED_INDEX_SCHEMA", 503)
        units = repo.find("content_units", {"resource_version_id": version_id},
                         order=("sequence_no", "id"), limit=self.settings.index_max_units + 1)
        if len(units) > self.settings.index_max_units:
            raise IndexingError("INDEX_LIMIT_EXCEEDED")
        projections = repo.find("etl_stage_runs", {"resource_version_id": version_id,
                                                   "stage": "MONGO_PROJECTION"},
                                order=("-created_at", "-id"))
        generation = len(projections)
        docs = self.store.source_units(version_id, self.settings.index_max_units + 1)
        by_id = {doc["_id"]: doc for doc in docs}
        if len(docs) != len(units) or len(by_id) != len(docs):
            raise IndexingError("CONTENT_NOT_READY")
        content, total_chars = [], 0
        for unit in units:
            body = repo.get("content_unit_texts", unit["id"])
            doc = by_id.get(unit["mongo_id"])
            if (not body or not doc or doc.get("content_unit_id") != unit["id"]
                    or doc.get("projection_generation") != generation
                    or doc.get("resource_version_id") != version_id
                    or doc.get("text") != body["text"]
                    or doc.get("page_from") != unit["page_from"]
                    or doc.get("page_to") != unit["page_to"]):
                raise IndexingError("CONTENT_NOT_READY")
            if unit["page_from"] is not None and unit["page_to"] is not None and unit["page_from"] > unit["page_to"]:
                raise IndexingError("CONTENT_NOT_READY")
            total_chars += len(body["text"])
            if total_chars > self.settings.index_max_source_chars:
                raise IndexingError("INDEX_LIMIT_EXCEEDED")
            content.append({**unit, "text": body["text"]})
        if not any(unit["text"].strip() for unit in content):
            raise IndexingError("EMPTY_CONTENT")
        fingerprint = digest({
            "version": version_id, "source_hash": version["source_hash"],
            "projection": projections[0]["id"], "generation": generation,
            "units": [{k: unit[k] for k in ("id", "sequence_no", "text", "page_from", "page_to",
                                            "review_status", "retrieval_eligible")} for unit in content],
            "algorithm": "word_window_v1", "bow": "unicode_nfc_casefold_tf_v1",
            "size": self.settings.chunk_size_words, "overlap": self.settings.chunk_overlap_words,
            "model": [self.embedding.model_id, self.embedding.version, self.embedding.dimension],
        })
        return version, generation, content, fingerprint

    def _expected(self, snapshot, run_id):
        version, generation, units, fingerprint = snapshot
        sequence = 0
        for unit in units:
            for ordinal, (start, end, text) in enumerate(word_windows(
                    unit["text"], self.settings.chunk_size_words, self.settings.chunk_overlap_words), 1):
                sequence += 1
                if sequence > self.settings.index_max_chunks:
                    raise IndexingError("INDEX_LIMIT_EXCEEDED")
                tf = bag_of_words(text)
                yield {
                    "_id": digest([version["id"], run_id, unit["id"], ordinal])[:30],
                    "resource_id": version["resource_id"], "resource_version_id": version["id"],
                    "content_unit_id": unit["id"], "index_run_id": run_id,
                    "source_projection_generation": generation, "chunk_sequence": sequence,
                    "unit_chunk_sequence": ordinal, "page_from": unit["page_from"], "page_to": unit["page_to"],
                    "review_status": unit["review_status"], "retrieval_eligible": unit["retrieval_eligible"],
                    "char_start": start, "char_end": end, "text": text, "bow_tf": tf,
                    "bow_length": sum(tf.values()), "embedding_model": self.embedding.model_id,
                    "embedding_version": self.embedding.version, "embedding_dimension": self.embedding.dimension,
                    "content_fingerprint": fingerprint,
                }

    def _verified(self, snapshot, run):
        docs = self.store.artifacts(snapshot[0]["id"], run["id"], self.settings.index_max_chunks + 1)
        expected = list(self._expected(snapshot, run["id"]))
        if not expected or len(docs) != len(expected):
            raise IndexingError("INDEX_ARTIFACT_INVALID", 503)
        for doc, wanted in zip(docs, expected, strict=True):
            if any(doc.get(k) != v for k, v in wanted.items()):
                raise IndexingError("INDEX_ARTIFACT_INVALID", 503)
            validate_vector(doc.get("embedding", []), self.embedding.dimension)
        return docs

    def enqueue(self, version_id, *, force=False):
        with self.uow.processing_lock(version_id) as acquired:
            if not acquired:
                raise IndexingError("INDEX_ALREADY_RUNNING")
            with self.uow() as repo:
                snapshot = self._snapshot(repo, version_id)
                runs = self._runs(repo, version_id)
                inflight = next((r for r in runs if r["status"] in ("PENDING", "RUNNING")), None)
                if inflight:
                    if force:
                        raise IndexingError("INDEX_ALREADY_RUNNING")
                    return self._queued(version_id, inflight, "IN_PROGRESS")
                if not self.embedding.available():
                    raise IndexingError("BLOCKED_EMBEDDING_RUNTIME", 503)
                self.store.ensure_ready()
                if not force:
                    if runs and runs[0]["status"] == "FAILED":
                        raise IndexingError("REINDEX_REQUIRED")
                    published = next((r for r in runs if r["status"] == "COMPLETED"
                                      and r["input_fingerprint"] == snapshot[3]), None)
                    if published:
                        self._verified(snapshot, published)
                        return self._queued(version_id, published, "ALREADY_INDEXED")
                stage = repo.insert("etl_stage_runs", {
                    "resource_version_id": version_id, "stage": "INDEX_BUILD", "status": "PENDING",
                    "started_at": now(), "created_at": now(), "input_fingerprint": snapshot[3],
                })
                return self._queued(version_id, stage, "QUEUED")

    @staticmethod
    def _queued(version_id, run, action):
        return {"resource_version_id": version_id, "index_run_id": run["id"],
                "index_status": "READY" if run["status"] == "COMPLETED" else run["status"],
                "action": action, "status_url": f"/api/v1/resource-versions/{version_id}/index-status"}

    def once(self):
        with self.uow() as repo:
            # Bounded queue scan; skip locked versions rather than stealing live work.
            runs = repo.index_candidates()
        for run in runs:
            if run["status"] not in ("PENDING", "RUNNING"):
                continue
            with self.uow.processing_lock(run["resource_version_id"]) as acquired:
                if not acquired:
                    continue
                with self.uow() as repo:
                    current = repo.get("etl_stage_runs", run["id"], lock=True)
                    if current["status"] not in ("PENDING", "RUNNING"):
                        continue
                    repo.update("etl_stage_runs", run["id"], {"status": "RUNNING", "started_at": now()})
                self._build(run)
                return True
        return False

    def _build(self, run):
        version_id = run["resource_version_id"]
        started = monotonic()
        try:
            self.embedding.prepare()
            self.store.ensure_ready()
            with self.uow() as repo:
                snapshot = self._snapshot(repo, version_id)
            if snapshot[3] != run["input_fingerprint"]:
                raise IndexingError("INDEX_SOURCE_CHANGED")
            for doc in self._expected(snapshot, run["id"]):
                if monotonic() - started > self.settings.index_timeout_seconds:
                    raise IndexingError("INDEX_LIMIT_EXCEEDED")
                vector = validate_vector(self.embedding.embed(doc["text"]), self.embedding.dimension)
                self.store.upsert({**doc, "embedding": vector, "created_at": run["created_at"], "updated_at": now()})
            self._verified(snapshot, run)
            with self.uow() as repo:
                current = self._snapshot(repo, version_id)
                if current[3] != snapshot[3]:
                    raise IndexingError("INDEX_SOURCE_CHANGED")
                repo.update("etl_stage_runs", run["id"], {"status": "COMPLETED", "finished_at": now(), "error_message": None})
        except Exception as exc:
            code = exc.code if isinstance(exc, IndexingError) else "INDEX_FAILED"
            with self.uow() as repo:
                repo.update("etl_stage_runs", run["id"], {"status": "FAILED", "finished_at": now(), "error_message": code})

    def status(self, version_id):
        with self.uow() as repo:
            _, state = self._version(repo, version_id)
            runs = self._runs(repo, version_id)
            try:
                snapshot = self._snapshot(repo, version_id) if state == "CONTENT_READY" else None
            except IndexingError as exc:
                if exc.code == "CONTENT_NOT_READY":
                    snapshot = None
                else:
                    raise
        latest = runs[0] if runs else None
        published = next((r for r in runs if snapshot and r["status"] == "COMPLETED"
                          and r["input_fingerprint"] == snapshot[3]), None)
        docs = self._verified(snapshot, published) if published else []
        status = "NOT_STARTED"
        code = latest["error_message"] if latest and latest["status"] == "FAILED" else None
        if not snapshot:
            status = "CONTENT_NOT_READY"
        elif not self.embedding.available():
            status, code = "BLOCKED_RUNTIME", "BLOCKED_EMBEDDING_RUNTIME"
        elif latest and latest["status"] in ("PENDING", "RUNNING", "FAILED"):
            status = latest["status"]
            if code == "BLOCKED_EMBEDDING_RUNTIME":
                status = "BLOCKED_RUNTIME"
        elif published:
            status = "READY"
        elif latest:
            status = "STALE"
        return {
            "resource_version_id": version_id, "processing_status": state, "index_status": status,
            "auto_enabled": self.settings.index_auto_enabled,
            "latest_run_id": latest["id"] if latest else None,
            "published_run_id": published["id"] if published else None,
            "chunk_count": len(docs), "bow_ready": bool(docs),
            "vector_ready": bool(docs) and self.embedding.available(),
            "algorithm": "word_window_v1", "chunk_size_words": self.settings.chunk_size_words,
            "chunk_overlap_words": self.settings.chunk_overlap_words,
            "embedding_model": self.embedding.model_id or None,
            "embedding_dimension": self.embedding.dimension if self.embedding.model_id else None,
            "started_at": latest["started_at"] if latest else None,
            "finished_at": latest["finished_at"] if latest else None,
            "error_code": code,
        }

    def chunks(self, version_id, *, offset=0, limit=10, content_unit_id=None):
        status = self.status(version_id)
        docs = []
        if status["published_run_id"] and status["index_status"] != "BLOCKED_RUNTIME":
            # Re-read snapshot and validate the exact published run; no unpublished artifact leak.
            with self.uow() as repo:
                snapshot = self._snapshot(repo, version_id)
                run = repo.get("etl_stage_runs", status["published_run_id"])
            if run["input_fingerprint"] == snapshot[3]:
                docs = self._verified(snapshot, run)
        if content_unit_id:
            docs = [d for d in docs if d["content_unit_id"] == content_unit_id]
        fields = ("content_unit_id", "chunk_sequence", "unit_chunk_sequence", "page_from", "page_to",
                  "char_start", "char_end", "text", "review_status", "retrieval_eligible",
                  "embedding_model", "embedding_version", "embedding_dimension")
        items = []
        for doc in docs[offset:offset + limit]:
            items.append({**{k: doc[k] for k in fields}, "id": doc["_id"],
                          "bow_top_terms": [{"term": k, "frequency": v} for k, v in
                                            sorted(doc["bow_tf"].items(), key=lambda item: (-item[1], item[0]))[:5]],
                          "bow_term_count": len(doc["bow_tf"]), "bow_length": doc["bow_length"], "vector_ready": True})
        return {"resource_version_id": version_id, "index_run_id": status["published_run_id"],
                "index_status": status["index_status"], "total": len(docs), "offset": offset,
                "limit": limit, "items": items}
