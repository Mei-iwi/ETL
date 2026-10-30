"""Linked original/translation views over durable extraction checkpoints."""

import hashlib
import re
import tempfile
from dataclasses import replace
from time import monotonic

from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.ports.processing import OCRResult

LANGUAGE_ALIASES = {
    "eng": "en",
    "vie": "vi",
    "fra": "fr",
    "fre": "fr",
    "deu": "de",
    "ger": "de",
    "jpn": "ja",
    "zho": "zh",
    "chi": "zh",
    "spa": "es",
    "kor": "ko",
    "rus": "ru",
    "und": "und",
    "mixed": "mixed",
}


def language_code(value):
    return LANGUAGE_ALIASES.get(value, value) if value else "und"


def detect_language(text):
    if sum(c.isalpha() for c in text) < 20:
        return "und"
    try:
        import langid

        return langid.classify(text)[0]
    except ImportError:
        return "und"


def split_spans(text, maximum=1800):
    """Prefer sentence/paragraph boundaries, retaining exact raw character spans."""
    start = 0
    while start < len(text):
        end = min(start + maximum, len(text))
        if end < len(text):
            boundaries = list(re.finditer(r"(?<=[.!?。！？])\s+|\n\s*\n", text[start:end]))
            if boundaries:
                end = start + boundaries[-1].end()
            else:
                space = text.rfind(" ", start + maximum // 2, end)
                if space > start:
                    end = space + 1
        yield start, end
        start = end


def paired_result(result, version_id, page, options):
    metadata = result.metadata
    if "paired_segments" in metadata:
        return result
    logical = metadata.get("logical_units")
    if logical is None:
        logical = [{"start": 0, "end": len(result.text), "locator": {"page_number": page}}]
    segments = []
    for unit in logical:
        locator = unit.get("locator", {})
        source_type = locator.get("source_type", metadata.get("source_type", "document"))
        spans = (
            [(0, unit["end"] - unit["start"])]
            if source_type in ("speech", "subtitle")
            else split_spans(result.text[unit["start"] : unit["end"]])
        )
        for left, right in spans:
            start, end = unit["start"] + left, unit["start"] + right
            text = result.text[start:end]
            if not text.strip():
                continue
            identifier = hashlib.sha256(
                f"{version_id}:{page}:{start}:{end}:{text}".encode()
            ).hexdigest()[:26]
            segments.append(
                {
                    **locator,
                    "segment_id": identifier,
                    "source_type": source_type,
                    "original_text": text,
                    "raw_start": start,
                    "raw_end": end,
                    "source_language": language_code(
                        options.get("source_language")
                        or locator.get("source_language")
                        or detect_language(text)
                    ),
                    "target_language": language_code(options.get("target_language", "vi")),
                    "translated_text": None,
                    "translation_status": "pending",
                    "generated": source_type == "visual_description",
                }
            )
    return replace(result, metadata={**metadata, "paired_segments": segments})


class BilingualService:
    def __init__(self, uow, settings, runner=None):
        self.uow, self.settings = uow, settings
        self.runner = runner

    def configure(self, version_id, options, *, retry_translation=False):
        with self.uow.processing_lock(version_id) as acquired:
            if not acquired:
                raise DomainError("PROCESSING_BUSY")
            with self.uow() as repo:
                version = repo.get("resource_versions", version_id, lock=True)
                if version is None:
                    raise DomainError("Resource version not found")
                resource = repo.get("master_learning_resources", version["resource_id"])
                if not resource or not resource["is_active"] or resource.get("deleted_at"):
                    raise DomainError("Resource version not found")
                if resource.get('source_origin') != 'LOCAL':
                    raise DomainError('EXTERNAL_SOURCE_PROCESSING_DISABLED')
                jobs = repo.find("ocr_jobs", {"resource_version_id": version_id}, limit=1)
                if any(job["status"] in ("PENDING", "RUNNING") for job in jobs):
                    raise DomainError("PROCESSING_BUSY")
                if jobs and not retry_translation:
                    raise DomainError("EXTRACTION_OPTIONS_LOCKED_USE_NEW_VERSION")
                if retry_translation and any(
                    key not in ("source_language", "target_language") for key in options
                ):
                    raise DomainError("TRANSLATION_OPTIONS_ONLY")
                current = version.get("processing_options") or {}
                count = current.get("translation_retries", 0) + int(retry_translation)
                if count > self.settings.translation_max_retries:
                    raise DomainError("TRANSLATION_RETRY_LIMIT")
                updated = {
                    **current,
                    **options,
                    "translation_pending": True,
                    "translation_retries": count,
                }
                repo.update(
                    "resource_versions",
                    version_id,
                    {"processing_options": updated, "updated_at": now()},
                )
        return {"status": "queued" if retry_translation else "configured", "options": updated}

    def translate_version(self, version_id):
        with self.uow.processing_lock(version_id) as acquired:
            if not acquired:
                return False
            with self.uow() as repo:
                version = repo.get("resource_versions", version_id)
                if not version:
                    return False
                resource = repo.get('master_learning_resources', version['resource_id'])
                if not resource or resource.get('source_origin') != 'LOCAL':
                    return False
                options = version.get("processing_options") or {}
                jobs = repo.find(
                    "ocr_jobs",
                    {"resource_version_id": version_id},
                    order=("-created_at", "-id"),
                    limit=1,
                )
                if not jobs or jobs[0]["status"] not in ("COMPLETED", "COMPLETED_WITH_ERRORS"):
                    return False
                tasks = repo.find(
                    "ocr_page_tasks",
                    {"job_id": jobs[0]["id"], "status": "SUCCEEDED"},
                    order=("page_num",),
                )
                repo.update(
                    "resource_versions",
                    version_id,
                    {"processing_options": {**options, "translation_state": "translating"}},
                )
            started = monotonic()
            statuses = []
            for task in tasks:
                with self.uow() as repo:
                    rows = repo.find("ocr_page_results", {"page_task_id": task["id"]}, limit=1)
                if not rows:
                    continue
                row = rows[0]
                metadata = row["result_metadata"] or {}
                if "paired_segments" not in metadata:
                    metadata = paired_result(
                        OCRResult(row["raw_text"], metadata=metadata),
                        version_id,
                        task["page_num"],
                        options,
                    ).metadata
                segments = metadata.get("paired_segments", [])
                for segment in segments:
                    target = language_code(
                        options.get("target_language", self.settings.translation_target_language)
                    )
                    source = language_code(
                        options.get("source_language") or segment["source_language"]
                    )
                    request = options.get("translation_retries", 0)
                    if (
                        segment.get("translation_request") == request
                        and segment["translation_status"] != "pending"
                    ):
                        statuses.append(segment["translation_status"])
                        continue
                    if segment.get("translated_text"):
                        segment["translation_history"] = [
                            *segment.get("translation_history", []),
                            {
                                key: segment.get(key)
                                for key in (
                                    "translated_text",
                                    "source_language",
                                    "target_language",
                                    "translation_provider",
                                    "translation_model_version",
                                    "translation_updated_at",
                                    "translation_request",
                                )
                            },
                        ]
                    segment.update(
                        source_language=source,
                        target_language=target,
                        translated_text=None,
                        translation_error=None,
                    )
                    if monotonic() - started >= self.settings.translation_job_timeout_seconds:
                        segment.update(
                            translation_status="failed", translation_error="TRANSLATION_JOB_TIMEOUT"
                        )
                    elif source == target:
                        segment.update(
                            translated_text=segment["original_text"],
                            translation_status="same_language",
                        )
                    elif self.settings.translation_provider == "disabled":
                        segment["translation_status"] = "disabled"
                    elif source in ("und", "mixed"):
                        segment["translation_status"] = "source_language_required"
                    elif (
                        not self.settings.translation_model_root
                        or not self.settings.translation_model_root.is_dir()
                    ):
                        segment.update(
                            translation_status="failed",
                            translation_error="TRANSLATION_MODEL_UNAVAILABLE",
                        )
                    else:
                        try:
                            with tempfile.TemporaryDirectory(prefix="etl-translate-") as directory:
                                if self.runner is None:
                                    raise DomainError("TRANSLATION_MODEL_UNAVAILABLE")
                                result = self.runner(
                                    "translate",
                                    {
                                        "text": segment["original_text"],
                                        "source": source,
                                        "target": target,
                                        "threads": self.settings.media_cpu_threads,
                                        "model_root": str(
                                            self.settings.translation_model_root.resolve()
                                        ),
                                        "scratch": directory,
                                    },
                                    self.settings.translation_timeout_seconds,
                                    directory,
                                )
                            if not result.get("text", "").strip():
                                raise DomainError("EMPTY_TRANSLATION")
                            segment.update(
                                translated_text=result["text"],
                                translation_status="completed",
                                translation_provider=result["provider"],
                                translation_version=result["version"],
                                translation_model_version=result.get("model_version"),
                            )
                        except DomainError as exc:
                            segment.update(translation_status="failed", translation_error=str(exc))
                    segment["translation_updated_at"] = now().isoformat()
                    segment["translation_request"] = request
                    statuses.append(segment["translation_status"])
                    # Checkpoint every segment; a killed worker can resume remaining segments.
                    with self.uow() as repo:
                        repo.update(
                            "ocr_page_results",
                            row["id"],
                            {"result_metadata": {**metadata, "paired_segments": segments}},
                        )
            with self.uow() as repo:
                state = (
                    "completed"
                    if all(s in ("completed", "same_language") for s in statuses)
                    else "partial"
                )
                repo.update(
                    "resource_versions",
                    version_id,
                    {
                        "processing_options": {
                            **options,
                            "translation_pending": False,
                            "translation_state": state,
                        }
                    },
                )
            return True

    def once(self):
        # Filter durable pending requests in SQL so older versions cannot starve.
        with self.uow() as repo:
            versions = repo.translation_candidates()
        for version in versions:
            if (version.get("processing_options") or {}).get("translation_pending"):
                with self.uow() as repo:
                    jobs = repo.find("ocr_jobs", {"resource_version_id": version["id"]}, limit=1)
                    if (
                        jobs
                        and jobs[0]["status"] == "COMPLETED_WITH_ERRORS"
                        and not (version["processing_options"] or {}).get("translation_retries")
                    ):
                        repo.update(
                            "resource_versions",
                            version["id"],
                            {
                                "processing_options": {
                                    **version["processing_options"],
                                    "translation_pending": False,
                                }
                            },
                        )
                        continue
                if self.translate_version(version["id"]):
                    return True
        return False

    def view(self, version_id, *, include_items=True, offset=0, limit=None, revision=None):
        if type(offset) is not int or not 0 <= offset <= 1000000 or (limit is not None
                and (type(limit) is not int or not 1 <= limit <= 100)):
            raise DomainError('INVALID_BILINGUAL_PAGE')
        with self.uow() as repo:
            version = repo.get("resource_versions", version_id)
            if not version:
                raise DomainError("Resource version not found")
            resource = repo.get('master_learning_resources', version['resource_id'])
            jobs = repo.find(
                "ocr_jobs",
                {"resource_version_id": version_id},
                order=("-created_at", "-id"),
                limit=1,
            )
            output, extraction = [], []
            for job in jobs:
                for task in repo.find("ocr_page_tasks", {"job_id": job["id"]}, order=("page_num", "id")):
                    rows = repo.find("ocr_page_results", {"page_task_id": task["id"]}, limit=1)
                    if not rows:
                        continue
                    metadata = rows[0]["result_metadata"] or {}
                    extraction.append(
                        {
                            key: metadata[key]
                            for key in ("media", "warnings", "extraction_status")
                            if key in metadata
                        }
                    )
                    if "paired_segments" not in metadata:
                        metadata = paired_result(
                            OCRResult(rows[0]["raw_text"], metadata=metadata),
                            version_id,
                            task["page_num"],
                            {"source_language": "und", **(version.get("processing_options") or {})},
                        ).metadata
                    output.extend(
                        {**segment, "extracted_at": rows[0]["created_at"].isoformat()}
                        for segment in metadata.get("paired_segments", [])
                    )
        # Storage paths stay server-side; downloads resolve through the authorized version.
        items = [
            {
                **{k: v for k, v in segment.items() if k != "frame"},
                **(
                    {
                        "frame_url": f"/api/v1/resource-versions/{version_id}/segments/{segment['segment_id']}/frame"
                    }
                    if segment.get("frame")
                    else {}
                ),
            }
            for segment in output
        ]
        result = {
            "resource_version_id": version_id,
            "resource_id": version["resource_id"],
            "source_backed": bool(resource and resource.get('source_origin') == 'POSTGRESQL'),
            "source_hash": version["source_hash"],
            "original_filename": (version.get("processing_options") or {}).get("original_filename"),
            "original_file_url": f"/api/v1/resource-versions/{version_id}/processing-source",
            "options": version.get("processing_options") or {},
            "extraction": extraction,
            "items": items,
        }
        if include_items and offset == 0 and limit is None and revision is None:
            return result  # Legacy callers and exports retain their full contract.
        from data_sync_etl.domain.indexing import digest

        # OCR metadata is stored as paired-segment blobs. Full reads are still
        # required; this projection reduces response bytes, not database I/O or
        # a measured end-to-end CPU cost (the revision also hashes the snapshot).
        current = digest([result, [job['id'] for job in jobs]])
        if revision is not None and revision != current:
            raise DomainError('BILINGUAL_REVISION_CHANGED')
        timed = [item for item in items if item.get('source_type') in ('speech', 'subtitle')
                 and item.get('start_time') is not None and item.get('end_time') is not None]
        size = limit or 20
        return {**result,
                'items': items[offset:offset + size] if include_items else [],
                'summary': {'total': len(items), 'translated_count': sum(bool(item.get('translated_text')) for item in items),
                            'timed_count': len(timed), 'timed_translated_count': sum(bool(item.get('translated_text')) for item in timed),
                            'revision': current},
                'pagination': {'offset': offset, 'limit': size, 'total': len(items)}}


def export_segments(view, format, translated=False):
    items = view["items"]
    key = "translated_text" if translated else "original_text"
    available = [s for s in items if s.get(key)]
    if not available:
        raise DomainError("EXPORT_NOT_AVAILABLE")
    if format == "txt":
        return "\n\n".join(s[key] for s in available)
    timed = [
        s
        for s in available
        if s["source_type"] in ("speech", "subtitle")
        and s.get("start_time") is not None
        and s.get("end_time") is not None
        and 0 <= s["start_time"] < s["end_time"]
    ]
    if not timed:
        raise DomainError("EXPORT_NOT_AVAILABLE")
    # Prefer the selected subtitle track over speech when both describe the same timeline.
    preferred = "subtitle" if any(s["source_type"] == "subtitle" for s in timed) else "speech"
    timed = sorted(
        (s for s in timed if s["source_type"] == preferred), key=lambda s: s["start_time"]
    )

    def stamp(seconds):
        milliseconds = round(seconds * 1000)
        hours, rest = divmod(milliseconds, 3600000)
        minutes, rest = divmod(rest, 60000)
        seconds, milliseconds = divmod(rest, 1000)
        return f"{hours:02}:{minutes:02}:{seconds:02}{',' if format == 'srt' else '.'}{milliseconds:03}"

    return (
        ("WEBVTT\n\n" if format == "vtt" else "")
        + "\n\n".join(
            f"{index}\n{stamp(s['start_time'])} --> {stamp(s['end_time'])}\n{s[key]}"
            for index, s in enumerate(timed, 1)
        )
        + "\n"
    )
