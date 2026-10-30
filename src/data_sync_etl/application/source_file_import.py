"""Durable source version discovery, import and stage-aware retry."""

import random
import re
from datetime import timedelta
from hashlib import sha256
from time import monotonic

from data_sync_etl.application.processing_state import processing_state
from data_sync_etl.domain.core import (
    DomainError,
    ProcessingRetryConflict,
    SourceFileError,
    UploadTooLargeError,
    new_id,
    now,
)
from data_sync_etl.domain.format_validation import (
    PUBLIC_PROCESSING_ERROR_CODES,
    FormatValidatorRegistry,
    ProcessingFormatError,
)
from data_sync_etl.domain.resource_deletion import resource_deleted
from data_sync_etl.logging import event
from data_sync_etl.ports.repositories import UnitOfWork
from data_sync_etl.ports.source_files import ReadOnlySourceFilePort, ReadOnlySourceVersionPort


class SourceImportConflict(DomainError):
    pass


class SourceImportNotFound(DomainError):
    pass


METADATA_ONLY_BUCKET = "__SOURCE_METADATA_ONLY__"
METADATA_ONLY_KEY_PREFIX = "__SOURCE_METADATA_ONLY__/"
METADATA_ONLY_ERROR = "SOURCE_METADATA_ONLY"


class SourceFileImportService:
    def __init__(self, source: ReadOnlySourceVersionPort, files: ReadOnlySourceFilePort,
                 storage, uow: UnitOfWork, process, retry_processing, settings, formats=None):
        self.source, self.files, self.storage, self.uow = source, files, storage, uow
        self.process, self.retry_processing, self.settings = process, retry_processing, settings
        self.formats = formats or FormatValidatorRegistry()
        self.validator = self.formats.find_validator('source.pdf')
        self._media_policy_scan_started = False
        self._file_count_cache = {}

    def _media_processing_enabled(self):
        return (not self.settings.source_metadata_media_enabled
                and self.settings.source_reference_enabled and self.settings.source_temporary_files_enabled)

    def _metadata_only_policy(self):
        """External SOURCE is metadata-only until both read gates are explicit."""
        return not (self.settings.source_reference_enabled and self.settings.source_temporary_files_enabled)

    @staticmethod
    def _metadata_locator(source_version_id, source_object_key=None):
        # Preserve only a bounded format hint for metadata filtering; never
        # persist the SOURCE filename/path itself.
        suffix = ''
        if isinstance(source_object_key, str) and '.' in source_object_key:
            candidate = source_object_key.rsplit('.', 1)[-1].lower()
            if candidate in {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv',
                             'png', 'jpg', 'jpeg', 'mp3', 'mp4'}:
                suffix = f'.{candidate}'
        return METADATA_ONLY_BUCKET, f"{METADATA_ONLY_KEY_PREFIX}{source_version_id}{suffix}"

    def _ensure_metadata_version(self, repo, row, source):
        """Register a searchable metadata version without copying or exposing SOURCE paths."""
        if row.get("target_version_id"):
            return row["target_version_id"]
        collision = repo.find("resource_versions", {
            "resource_id": row["resource_id"], "version_number": row["source_version_number"]}, limit=1)
        if collision:
            raise SourceFileError("BLOCKED_SOURCE_MAPPING")
        parent = None
        parent_id = source.get("parent_version_id")
        if parent_id:
            parent_rows = repo.find("source_file_imports", {
                "source_system": row["source_system"], "source_version_id": parent_id}, limit=1)
            if parent_rows and parent_rows[0].get("target_version_id"):
                parent = repo.get("resource_versions", parent_rows[0]["target_version_id"])
                if parent is not None:
                    if parent['resource_id'] != row['resource_id']:
                        raise SourceFileError('BLOCKED_SOURCE_MAPPING')
                    parent = parent["id"]
        bucket, key = self._metadata_locator(row["source_version_id"], source.get('storage_object_key'))
        version = repo.insert("resource_versions", {
            "resource_id": row["resource_id"], "version_number": row["source_version_number"],
            "parent_version_id": parent, "storage_bucket": bucket, "storage_object_key": key,
            "source_hash": row["source_hash"].lower(), "lifecycle_status": "METADATA_ONLY",
        })
        repo.update("source_file_imports", row["id"], {
            "target_version_id": version["id"], "status": "METADATA_ONLY",
            "last_error_code": METADATA_ONLY_ERROR, "updated_at": now(),
        })
        return version["id"]

    def availability(self):
        storage_error = None
        try:
            self.files.check()
        except SourceFileError as exc:
            storage_error = exc.code
        error = storage_error if hasattr(self.source, 'version_page') else 'BLOCKED_SOURCE_DATABASE'
        if hasattr(self.source, 'version_page') and not self.settings.source_reference_enabled:
            error = 'SOURCE_REFERENCE_DISABLED'
        elif hasattr(self.source, 'version_page') and not self.settings.source_temporary_files_enabled:
            error = 'SOURCE_TEMP_POLICY_REQUIRED'
        with self.uow() as repo:
            worker = repo.get("sync_checkpoints", "source-file-worker")
        healthy = bool(worker and worker["last_id"] and worker["updated_at"] > now() - timedelta(seconds=30))
        backends = self.files.readiness() if hasattr(self.files, 'readiness') else {
            'local': {'status': 'LOCAL_READY' if storage_error is None else 'LOCAL_UNAVAILABLE', 'error_code': storage_error}}
        return {"file_sync_supported": error is None, "file_sync_error": error, 'file_backends': backends,
                "file_worker_healthy": healthy}

    def discover_once(self):
        if not hasattr(self.source, "version_page"):
            return 0
        with self.uow.processing_lock("source-file-discovery") as acquired:
            if not acquired:
                return 0
            with self.uow() as repo:
                runs = repo.find("sync_runs", {"stream_name": "resources", "status": "COMPLETED"},
                                 order=("-finished_at",), limit=1)
                if not runs:
                    return 0
                latest = runs[0]["finished_at"]
                repo.ensure_checkpoint("source-file-discovery")
                checkpoint = repo.get("sync_checkpoints", "source-file-discovery", lock=True)
                auto = repo.get("sync_auto_streams", "resources")
                active_scan = checkpoint["last_id"] is not None
                new_master = checkpoint["last_updated_at"] is None or latest > checkpoint["last_updated_at"]
                due = bool(auto and auto["enabled"] and checkpoint["updated_at"] <= now()
                           - timedelta(seconds=self.settings.source_file_reconcile_seconds))
                # A restart with processing enabled must revisit terminal
                # metadata-only imports, even without a new SOURCE delta or
                # automatic Master Sync. Reuse the bounded discovery cursor.
                policy_scan = self._media_processing_enabled() and not self._media_policy_scan_started
                if not (active_scan or new_master or due or policy_scan):
                    return 0
                after = checkpoint["last_id"] if active_scan and not policy_scan else None
            # Only read metadata here; never copy a blob in the Master Sync transaction.
            page = self.source.version_page(after=after or None, size=min(100, self.settings.sync_batch_size))
            with self.uow() as repo:
                for source in page["items"]:
                    self._discover(repo, source)
                repo.update("sync_checkpoints", "source-file-discovery", {
                    "last_id": page["next_cursor"], "updated_at": now(),
                    # Changes during a resumed scan trigger another reconciliation afterwards.
                    "last_updated_at": checkpoint["last_updated_at"] if active_scan and not policy_scan else latest,
                })
            if policy_scan:
                self._media_policy_scan_started = True
            return len(page["items"])

    def _resume_metadata_media(self, repo, row, source):
        if (not self._media_processing_enabled() or row['status'] != 'METADATA_ONLY'
                or not row['target_version_id'] or row['processing_job_id'] is not None
                or row['last_error_code'] not in (None, METADATA_ONLY_ERROR, 'UNSUPPORTED_SOURCE_FORMAT')
                or not source.get('resource_active', True) or source.get('resource_deleted_at') is not None
                or source['source_lifecycle_status'] not in self.settings.source_file_allowed_statuses):
            return
        resource = repo.get('master_learning_resources', row['resource_id'])
        target = repo.get('resource_versions', row['target_version_id'])
        if (resource is None or not resource['is_active'] or resource_deleted(resource)
                or resource.get('source_origin') != 'POSTGRESQL' or target is None
                or target['resource_id'] != row['resource_id']
                or target['lifecycle_status'] not in ('METADATA_ONLY', 'INGESTED', 'APPROVED', 'PUBLISHED')
                or target['source_hash'].lower() != row['source_hash'].lower()):
            return
        # Preserve the target, retry counters and historical jobs. The existing
        # leased claim + resolver revalidates rights/hash before idempotent job
        # creation; never decode media or load a model during discovery.
        repo.update('source_file_imports', row['id'], {
            'status': 'PENDING', 'last_error_code': None, 'next_retry_at': None,
            'claim_token': None, 'lease_expires_at': None, 'updated_at': now(),
        })
        repo.update('resource_versions', target['id'], {
            'storage_bucket': source['storage_bucket'], 'storage_object_key': source['storage_object_key'],
        })

    def _discover(self, repo, source):
        unsupported_media = False  # MP3/MP4 now share the normal processing pipeline.
        metadata_only = self._metadata_only_policy()
        existing = repo.find("source_file_imports", {
            "source_system": "postgresql", "source_version_id": source["source_version_id"]}, limit=1)
        if existing:
            row = repo.get("source_file_imports", existing[0]["id"], lock=True)
            lineage_changed = False
            if row["target_version_id"]:
                target = repo.get("resource_versions", row["target_version_id"])
                parent = repo.find("source_file_imports", {
                    "source_system": row["source_system"], "target_version_id": target["parent_version_id"]}, limit=1
                ) if target and target["parent_version_id"] else []
                expected = source.get("parent_version_id")
                lineage_changed = target is None or (expected is None and target["parent_version_id"] is not None) or (
                    expected is not None and (not parent or parent[0]["source_version_id"] != expected
                                             or parent[0]["resource_id"] != row["resource_id"]))
                if metadata_only and target is not None and expected is not None:
                    # A metadata page can arrive before its parent. Keep the
                    # searchable child and attach the parent when its mapping
                    # becomes available; do not expose a false mutation.
                    parent_rows = repo.find("source_file_imports", {
                        "source_system": row["source_system"], "source_version_id": expected}, limit=1)
                    if parent_rows and parent_rows[0].get("target_version_id"):
                        parent_target = repo.get("resource_versions", parent_rows[0]["target_version_id"])
                        if parent_target and target["parent_version_id"] != parent_target["id"]:
                            repo.update("resource_versions", target["id"], {"parent_version_id": parent_target["id"]})
                        lineage_changed = False
                    elif target["parent_version_id"] is None:
                        lineage_changed = False
            raw_changed = (row["source_hash"].lower(), row["resource_id"], row["source_version_number"], row['storage_bucket'], row['storage_object_key']) != (
                source["source_hash"].lower(), source["resource_id"], source["source_version_number"], source['storage_bucket'], source['storage_object_key']
            )
            metadata_changed = (row["source_hash"].lower(), row["resource_id"], row["source_version_number"]) != (
                source["source_hash"].lower(), source["resource_id"], source["source_version_number"])
            # A synthetic metadata locator was never a SOURCE storage snapshot.
            # Enabling explicit content access must resolve its real locator,
            # while still rejecting changed resource/version/hash/lineage.
            placeholder = row['storage_bucket'] == METADATA_ONLY_BUCKET and row['status'] == 'METADATA_ONLY'
            if lineage_changed or metadata_changed or (raw_changed and not metadata_only and not placeholder):
                repo.update("source_file_imports", row["id"], {
                    "status": "FAILED", "last_error_code": "SOURCE_VERSION_MUTATED",
                    "claim_token": None, "lease_expires_at": None, "updated_at": now(),
                })
            else:
                self._resume_metadata_media(repo, row, source)
                if (self.settings.source_metadata_media_enabled and row['last_error_code'] == 'UNSUPPORTED_SOURCE_FORMAT'
                        and row['target_version_id'] is None and row['status'] == 'FAILED'):
                    repo.update('source_file_imports', row['id'], {'status': 'PENDING', 'last_error_code': None})
                fields = {
                    "resource_title": source["resource_title"],
                    "source_lifecycle_status": source["source_lifecycle_status"],
                    "source_resource_active": source.get("resource_active", True) and source.get("resource_deleted_at") is None,
                    "source_updated_at": source["updated_at"], "updated_at": now(),
                }
                if not metadata_only:
                    fields.update({"storage_bucket": source["storage_bucket"], "storage_object_key": source["storage_object_key"]})
                repo.update("source_file_imports", row["id"], fields)
                if metadata_only:
                    self._ensure_metadata_version(repo, repo.get("source_file_imports", row["id"]), source)
            return
        bucket, key = self._metadata_locator(source["source_version_id"], source.get('storage_object_key')) if metadata_only else (
            source["storage_bucket"], source["storage_object_key"])
        inserted = repo.insert("source_file_imports", {
            "status": "METADATA_ONLY" if metadata_only else ("FAILED" if unsupported_media else "PENDING"),
            "last_error_code": METADATA_ONLY_ERROR if metadata_only else ("UNSUPPORTED_SOURCE_FORMAT" if unsupported_media else None),
            "source_system": "postgresql", "source_version_id": source["source_version_id"],
            "resource_id": source["resource_id"], "resource_title": source["resource_title"],
            "source_version_number": source["source_version_number"],
            "source_lifecycle_status": source["source_lifecycle_status"],
            "source_resource_active": source.get("resource_active", True) and source.get("resource_deleted_at") is None,
            "source_updated_at": source["updated_at"], "source_hash": source["source_hash"].lower(),
            "storage_bucket": bucket, "storage_object_key": key,
        })
        if metadata_only:
            self._ensure_metadata_version(repo, inserted, source)

    def claim(self, worker_id=None):
        stamp = now()
        with self.uow() as repo:
            repo.ensure_checkpoint("source-file-worker")
            marker = sha256(worker_id.encode()).hexdigest()[:30] if worker_id else None
            repo.update("sync_checkpoints", "source-file-worker", {"updated_at": stamp, "last_id": marker})
            return repo.claim_source_import(stamp, new_id(), stamp + timedelta(
                seconds=self.settings.source_file_import_lease_seconds),
                max_active=self.settings.source_file_import_max_active)

    def worker_stopped(self, worker_id):
        with self.uow() as repo:
            checkpoint = repo.get("sync_checkpoints", "source-file-worker", lock=True)
            if checkpoint and checkpoint["last_id"] == sha256(worker_id.encode()).hexdigest()[:30]:
                repo.update("sync_checkpoints", "source-file-worker", {"last_id": None})

    def _owned(self, repo, claimed):
        row = repo.get("source_file_imports", claimed["id"], lock=True)
        if (not row or row["claim_token"] != claimed["claim_token"]
                or row["status"] != "FETCHING" or row["lease_expires_at"] <= now()):
            raise SourceImportConflict("Import claim expired")
        return row

    def heartbeat(self, claimed):
        with self.uow() as repo:
            row = self._owned(repo, claimed)
            repo.update("source_file_imports", row["id"], {"lease_expires_at": now()
                        + timedelta(seconds=self.settings.source_file_import_lease_seconds)})
            repo.update("sync_checkpoints", "source-file-worker", {"updated_at": now()})

    @staticmethod
    def _master(repo, resource_id):
        resource = repo.get("master_learning_resources", resource_id, lock=True)
        if not resource or not resource["is_active"] or resource_deleted(resource):
            raise SourceFileError("WAITING_FOR_MASTER", transient=True)
        return resource

    def _source_allowed(self, row):
        return (row["source_resource_active"]
                and row["source_lifecycle_status"] in self.settings.source_file_allowed_statuses)

    def execute(self, claimed):
        try:
            if claimed.get('status') == 'METADATA_ONLY':
                return
            self.resolver._enabled()
            self.resolver.files = self.files
            media = claimed['storage_object_key'].lower().endswith(('.mp3', '.mp4'))
            if claimed["attempts"] > self.settings.source_file_import_max_attempts:
                raise SourceFileError("WORKER_RECOVERY_EXHAUSTED", transient=True)
            with self.uow() as repo:
                current = self._owned(repo, claimed)
                self._master(repo, current['resource_id'])
                if not self._source_allowed(current):
                    raise SourceFileError('BLOCKED_SOURCE_POLICY')
            if not claimed["target_version_id"]:
                self._import(claimed)
            else:
                reference = self.resolver.resolve(claimed['target_version_id'])
                with self.resolver.temporary_path(reference):
                    pass
                with self.uow() as repo:
                    row = self._owned(repo, claimed)
                    repo.update('resource_versions', row['target_version_id'], {'lifecycle_status': 'INGESTED'})
            with self.uow() as repo:
                row = self._owned(repo, claimed)
                self._master(repo, row["resource_id"])
                if not self._source_allowed(row):
                    raise SourceFileError("BLOCKED_SOURCE_POLICY")
                if media and self.settings.source_metadata_media_enabled:
                    # Registration verified bytes through the existing resolver.
                    # Terminal import state is independent of content readiness.
                    repo.update('source_file_imports', row['id'], {
                        'status': 'METADATA_ONLY', 'last_error_code': 'UNSUPPORTED_SOURCE_FORMAT',
                        'claim_token': None, 'lease_expires_at': None, 'next_retry_at': None,
                        'updated_at': now(),
                    })
                    return
            if row["processing_job_id"]:
                if row["processing_retry_requested"]:
                    self.retry_processing.run(row["processing_job_id"],
                        fence=lambda repo: self._owned(repo, claimed))
                job_id = row["processing_job_id"]
            else:
                job_id = self.process.run(row["target_version_id"],
                    fence=lambda repo: self._owned(repo, claimed))["job_id"]
            with self.uow() as repo:
                row = self._owned(repo, claimed)
                repo.update("source_file_imports", row["id"], {
                    "processing_job_id": job_id, "status": "PROCESSING", "last_error_code": None,
                    "processing_retry_requested": False,
                    "claim_token": None, "lease_expires_at": None, "next_retry_at": None, "updated_at": now(),
                })
            self._report(claimed, 'PROCESSING')
        except SourceImportConflict:
            return
        except SourceFileError as exc:
            self._fail(claimed, exc.code, exc.transient)
        except UploadTooLargeError:
            self._fail(claimed, "FILE_TOO_LARGE", False)
        except ProcessingRetryConflict:
            self._fail(claimed, "PROCESSING_RETRY_BLOCKED", False)
        except ProcessingFormatError as exc:
            self._fail(claimed, exc.code, False)
        except OSError:
            self._fail(claimed, "SOURCE_STORAGE_UNAVAILABLE", True)
        except Exception:
            self._fail(claimed, "IMPORT_OPERATION_FAILED", True)

    def _lineage(self, row):
        # Re-read SOURCE on every attempt; never persist a guessed dependency.
        if row["source_system"] != "postgresql" or not hasattr(self.source, "get_version"):
            raise SourceFileError("BLOCKED_SOURCE_MAPPING")
        source = self.source.get_version(row["source_version_id"])
        if source is None:
            raise SourceFileError("SOURCE_VERSION_MISSING")
        if (source["resource_id"], source["source_version_number"], source["source_hash"].lower()) != (
                row["resource_id"], row["source_version_number"], row["source_hash"]):
            raise SourceFileError("SOURCE_VERSION_MUTATED")
        if (not source.get("resource_active", True) or source.get("resource_deleted_at") is not None
                or source["source_lifecycle_status"] not in self.settings.source_file_allowed_statuses):
            raise SourceFileError("BLOCKED_SOURCE_POLICY")
        parent_id = source["parent_version_id"]
        current, seen = parent_id, {row["source_version_id"]}
        # Protect against malicious cycles and unbounded source chains without DDL.
        for _ in range(128):
            if current is None:
                return parent_id
            if current in seen:
                raise SourceFileError("SOURCE_LINEAGE_CYCLE")
            seen.add(current)
            parent = self.source.get_version(current)
            if parent is None:
                raise SourceFileError("PARENT_SOURCE_MISSING")
            if parent["resource_id"] != row["resource_id"]:
                raise SourceFileError("PARENT_SOURCE_RESOURCE")
            if (not parent.get("resource_active", True) or parent.get("resource_deleted_at") is not None
                    or parent["source_lifecycle_status"] not in self.settings.source_file_allowed_statuses):
                raise SourceFileError("PARENT_SOURCE_POLICY")
            current = parent["parent_version_id"]
        raise SourceFileError("SOURCE_LINEAGE_DEPTH_LIMIT")

    def _parent(self, repo, row, parent_id):
        if parent_id is None:
            return None
        mappings = repo.find("source_file_imports", {
            "source_system": row["source_system"], "source_version_id": parent_id}, limit=1)
        if not mappings:
            raise SourceFileError("PARENT_PENDING", transient=True)
        parent = mappings[0]
        if parent["resource_id"] != row["resource_id"]:
            raise SourceFileError("PARENT_MAPPING_INVALID")
        if parent["status"] in {"FAILED", "RETRY_EXHAUSTED"}:
            raise SourceFileError("PARENT_MAPPING_FAILED")
        # IMPORTED/PROCESSING is a committed file mapping; OCR completion is independent.
        if parent["status"] not in {"IMPORTED", "PROCESSING", "METADATA_ONLY"}:
            raise SourceFileError("PARENT_PENDING", transient=True)
        target = repo.get("resource_versions", parent["target_version_id"]) if parent["target_version_id"] else None
        if (target is None or target["resource_id"] != row["resource_id"]
                or target["version_number"] != parent["source_version_number"]
                or target["source_hash"] != parent["source_hash"]):
            raise SourceFileError("PARENT_MAPPING_INVALID")
        source_parent = self.source.get_version(parent_id)
        if source_parent is None or (source_parent["source_version_number"], source_parent["source_hash"].lower()) != (
                parent["source_version_number"], parent["source_hash"]):
            raise SourceFileError("PARENT_MAPPING_INVALID")
        seen, ancestor, source_ancestor = set(), target, source_parent
        for _ in range(128):
            if (ancestor["id"] in seen or ancestor["resource_id"] != row["resource_id"]
                    or ancestor["version_number"] != source_ancestor["source_version_number"]
                    or ancestor["source_hash"] != source_ancestor["source_hash"].lower()):
                raise SourceFileError("PARENT_MAPPING_INVALID")
            seen.add(ancestor["id"])
            expected = source_ancestor["parent_version_id"]
            if ancestor["parent_version_id"] is None:
                if expected is not None:
                    raise SourceFileError("PARENT_MAPPING_INVALID")
                return target["id"]
            if expected is None:
                raise SourceFileError("PARENT_MAPPING_INVALID")
            historical = repo.find("source_file_imports", {
                "source_system": row["source_system"], "target_version_id": ancestor["parent_version_id"]}, limit=1)
            if (not historical or historical[0]["source_version_id"] != expected
                    or historical[0]["resource_id"] != row["resource_id"]
                    or historical[0]["status"] not in {"IMPORTED", "PROCESSING", "METADATA_ONLY"}):
                raise SourceFileError("PARENT_MAPPING_INVALID")
            source_ancestor = self.source.get_version(expected)
            if source_ancestor is None:
                raise SourceFileError("PARENT_MAPPING_INVALID")
            ancestor = repo.get("resource_versions", ancestor["parent_version_id"])
            if ancestor is None:
                raise SourceFileError("PARENT_MAPPING_INVALID")
        raise SourceFileError("SOURCE_LINEAGE_DEPTH_LIMIT")

    def _import(self, claimed):
        with self.uow() as repo:
            row = self._owned(repo, claimed)
            if not self._source_allowed(row):
                raise SourceFileError("BLOCKED_SOURCE_POLICY")
            self._master(repo, row["resource_id"])
        parent_id = self._lineage(row)
        with self.uow() as repo:
            self._owned(repo, claimed)
            self._parent(repo, row, parent_id)
        if not self._source_allowed(row):
            raise SourceFileError("BLOCKED_SOURCE_POLICY")
        if not re.fullmatch(r"[0-9a-f]{64}", row["source_hash"]):
            raise SourceFileError("INVALID_SOURCE_HASH")
        if row["source_version_number"] < 1:
            raise SourceFileError("BLOCKED_SOURCE_MAPPING")
        if '://' in row['storage_bucket'] or '://' in row['storage_object_key']:
            raise SourceFileError('INVALID_SOURCE_LINK')
        try:
            validator = self.formats.find_validator(row['storage_object_key'])
        except DomainError:
            raise SourceFileError('UNSUPPORTED_SOURCE_FORMAT') from None
        if not validator.text_extraction_supported and not (
                self.settings.source_metadata_media_enabled and validator.extension in ('.mp3', '.mp4')):
            raise SourceFileError('UNSUPPORTED_SOURCE_FORMAT')

        reference = self.resolver.import_reference(row)
        with self.resolver.temporary_path(reference):
            pass  # Verify bytes and canonical metadata before publishing a mapping.
        with self.uow() as repo:
            row = self._owned(repo, claimed)
            self._master(repo, row["resource_id"])
            if not self._source_allowed(row):
                raise SourceFileError("BLOCKED_SOURCE_POLICY")
            if row["target_version_id"]:
                return
            collision = repo.find("resource_versions", {
                "resource_id": row["resource_id"], "version_number": row["source_version_number"]}, limit=1)
            if collision:
                raise SourceFileError("BLOCKED_SOURCE_MAPPING")
            if self._lineage(row) != parent_id:
                raise SourceFileError("SOURCE_VERSION_MUTATED")
            parent = self._parent(repo, row, parent_id)
            version = repo.insert("resource_versions", {
                "resource_id": row["resource_id"], "version_number": row["source_version_number"],
                "parent_version_id": parent,
                "storage_bucket": reference.bucket, "storage_object_key": reference.opaque_locator,
                "source_hash": reference.expected_sha256, "lifecycle_status": "INGESTED",
            })
            repo.update("source_file_imports", row["id"], {
                "target_version_id": version["id"], "updated_at": now(),
            })

    def _fail(self, claimed, code, transient):
        with self.uow() as repo:
            try:
                row = self._owned(repo, claimed)
            except SourceImportConflict:
                return
            resource = repo.get('master_learning_resources', row['resource_id'], lock=True)
            if resource_deleted(resource):
                repo.update('source_file_imports', row['id'], {
                    'status': 'RETRY_WAIT', 'attempts': max(0, row['attempts'] - 1),
                    'claim_token': None, 'lease_expires_at': None, 'next_retry_at': None,
                    'last_error_code': 'RESOURCE_SOFT_DELETED', 'updated_at': now()})
                return
            exhausted = row["attempts"] >= self.settings.source_file_import_max_attempts
            delay = min(3600, self.settings.source_file_import_retry_seconds * 2 ** min(row["attempts"] - 1, 10))
            repo.update("source_file_imports", row["id"], {
                "status": "RETRY_EXHAUSTED" if transient and exhausted else "RETRY_WAIT" if transient else "FAILED",
                "last_error_code": code, "claim_token": None, "lease_expires_at": None,
                "next_retry_at": now() + timedelta(seconds=min(3600, delay + random.uniform(0, delay / 4)))
                if transient and not exhausted else None, "updated_at": now(),
            })
        self._report(claimed, 'FAILED', code)

    def _report(self, row, action, code=None):
        try:
            provider = self.files.provider(row['storage_bucket']) if hasattr(self.files, 'provider') else 'local'
        except SourceFileError:
            provider = 'unmapped'
        event(stage='source_import', action=action, coverage={
            'provider': provider, 'error_code': code,
            'reference': sha256(row['source_version_id'].encode()).hexdigest()[:12]})

    def _processing(self, repo, row):
        if not row["processing_job_id"]:
            return None, False
        job = repo.get("ocr_jobs", row["processing_job_id"])
        if not job:
            return None, False
        state = processing_state(repo, job)
        if job["status"] == "RUNNING":
            state = "OCR_RUNNING"
        exhausted = state == "OCR_FAILED"
        if state == "FINALIZATION_FAILED":
            stages = repo.find("etl_stage_runs", {"resource_version_id": row["target_version_id"]},
                               order=("-created_at", "-id"))
            projections = [s for s in stages if s["stage"] == "MONGO_PROJECTION"]
            cycles = [s for s in stages if s["stage"] == "FINALIZATION_RETRY"]
            baseline = int(cycles[0]["input_fingerprint"], 16) if cycles else 0
            attempts = projections[:max(0, len(projections) - baseline)]
            exhausted = len(attempts) >= self.settings.finalization_max_attempts
        count = sum(repo.count("etl_stage_runs", {"resource_version_id": row["target_version_id"], "stage": stage})
                    for stage in ("OCR_RETRY", "FINALIZATION_RETRY"))
        return state, exhausted and count < self.settings.processing_manual_retry_limit

    def _eligible(self, repo, row):
        if not self._source_allowed(row):
            return False
        resource = repo.get('master_learning_resources', row['resource_id'])
        if resource is None or not resource['is_active'] or resource_deleted(resource):
            return False
        if (row["storage_object_key"].lower().endswith(('.mp3', '.mp4'))
                and self.settings.source_metadata_media_enabled):
            return False
        if row["last_error_code"] in {"SOURCE_VERSION_MUTATED", "BLOCKED_SOURCE_MAPPING", "UNSUPPORTED_SOURCE_FORMAT"}:
            return False
        if row["status"] in {"FAILED", "RETRY_EXHAUSTED"}:
            return row["manual_retries"] < self.settings.processing_manual_retry_limit
        return row["status"] == "PROCESSING" and self._processing(repo, row)[1]

    def list_files(self, after=None, size=20, failed_only=False, retry_eligible=None, include_total=False,
                   integrity_only=False, evidence_reader=None):
        availability = self.availability()
        scan_cursor = None
        with self.uow() as repo:
            if integrity_only:
                if evidence_reader is None:
                    raise DomainError('INTEGRITY_EVIDENCE_REQUIRED')
                rows, scan_after = [], after
                # Bound each request even when most of a large repository is verified.
                for _ in range(10):
                    batch = repo.source_import_page(scan_after, 100)
                    for row in batch:
                        if not evidence_reader(repo, row)['recent_sha256_evidence']:
                            rows.append(row)
                            if len(rows) > size:
                                break
                    if len(rows) > size or len(batch) < 100:
                        scan_cursor = None
                        break
                    scan_after = batch[-1]['source_version_id']
                    scan_cursor = scan_after
            elif retry_eligible is None:
                rows = repo.source_import_page(after, size + 1, failed_only=failed_only)
            else:
                # Filter before pagination using the same eligibility rules as the Retry action.
                # Scan by source ID, so skipped groups never produce empty intermediate pages.
                rows = []
                scan_after = after
                while len(rows) <= size:
                    batch = repo.source_import_page(scan_after, 100, failed_only=True)
                    for row in batch:
                        eligible = (availability["file_sync_supported"] or row["target_version_id"] is not None) \
                            and self._eligible(repo, row)
                        if eligible == retry_eligible:
                            rows.append(row)
                            if len(rows) > size:
                                break
                    if len(rows) > size or len(batch) < 100:
                        break
                    scan_after = batch[-1]["source_version_id"]
            undiscovered = not rows and not repo.find('source_file_imports', limit=1)
            total = None
            if include_total and not undiscovered and not integrity_only:
                key = (failed_only, retry_eligible, availability['file_sync_supported'])
                cached = self._file_count_cache.get(key)
                if cached and monotonic() - cached[0] < 30:
                    total = cached[1]
                elif retry_eligible is None:
                    total = repo.source_import_page(failed_only=failed_only, count_only=True)
                else:
                    # Exact filtered count, bounded keyset pages; requested only on
                    # navigation/filter changes, never by background polling.
                    total, count_after = 0, None
                    while True:
                        group = repo.source_import_page(count_after, 100, failed_only=True)
                        total += sum(bool((availability['file_sync_supported'] or r['target_version_id'] is not None)
                            and self._eligible(repo, r)) == retry_eligible for r in group)
                        if len(group) < 100:
                            break
                        count_after = group[-1]['source_version_id']
                self._file_count_cache[key] = (monotonic(), total)
            items = []
            for row in rows[:size]:
                state, _ = self._processing(repo, row)
                failed = repo.find('ocr_page_tasks', {'job_id': row['processing_job_id'], 'status': 'FAILED'}, limit=1) if row['processing_job_id'] else []
                processing_code = failed[0]['error_message'] if failed and failed[0]['error_message'] in PUBLIC_PROCESSING_ERROR_CODES else None
                items.append({
                    "import_job_id": row["id"], "source_version_id": row["source_version_id"],
                    "resource_id": row["resource_id"], "resource_title": row["resource_title"],
                    "source_version_number": row["source_version_number"],
                    "source_lifecycle_status": row["source_lifecycle_status"], "updated_at": row["updated_at"],
                    "import_status": row["status"], "etl_status": state,
                    "retry_count": row["attempts"], "last_error_code": row["last_error_code"] or processing_code,
                    "retry_eligible": (availability["file_sync_supported"] or row["target_version_id"] is not None)
                    and self._eligible(repo, row),
                    "target_version_id": row["target_version_id"], "processing_job_id": row["processing_job_id"],
                    "integrity": evidence_reader(repo, row) if evidence_reader else None,
                })
        if not failed_only and undiscovered and hasattr(self.source, "list_versions"):
            # Preserve the initial metadata view before the worker has discovered any jobs.
            total = self.source.version_count() if include_total and hasattr(self.source, 'version_count') else None
            return {**self.source.list_versions(after=after, size=size), 'total': total,
                    'total_pages': (total + size - 1) // size if total is not None else None, **availability}
        return {"items": items, "next_cursor": rows[size - 1]["source_version_id"] if len(rows) > size else scan_cursor,
                'total': total, 'total_pages': (total + size - 1) // size if total is not None else None, **availability}

    def retry(self, file_id):
        available = self.availability()["file_sync_supported"]
        with self.uow() as repo:
            row = repo.get("source_file_imports", file_id, lock=True)
            if row is None:
                raise SourceImportNotFound("Import job not found")
            if not self._eligible(repo, row):
                raise SourceImportConflict("Import job cannot be retried")
            if not available and row["target_version_id"] is None:
                raise SourceImportConflict("Source file storage unavailable")
            repo.update("source_file_imports", file_id, {
                "status": "RETRY_PROCESSING" if row["status"] == "PROCESSING" else "PENDING",
                "processing_retry_requested": row["status"] == "PROCESSING" or row["processing_retry_requested"],
                "attempts": 0, "manual_retries": row["manual_retries"] + 1,
                "next_retry_at": None, "last_error_code": None, "updated_at": now(),
            })
        self._file_count_cache.clear()
        return {"import_job_id": file_id, "action": "QUEUED"}

    def retry_failed(self, after=None, size=100):
        with self.uow() as repo:
            rows = repo.source_import_page(after, size + 1, retry_candidates=True)
        queued = skipped = conflicts = 0
        for row in rows[:size]:
            with self.uow() as repo:
                eligible = self._eligible(repo, row)
            if not eligible:
                skipped += 1
                continue
            try:
                self.retry(row["id"])
                queued += 1
            except (SourceImportConflict, SourceImportNotFound):
                conflicts += 1
        return {"accepted_count": queued, "skipped_count": skipped, "conflict_count": conflicts,
                "next_cursor": rows[size - 1]["source_version_id"] if len(rows) > size else None}

    def precheck(self, mongo_client=None):
        result = dict.fromkeys(["DB_SOURCE", "FILE_ROOT", "FILE_READ", "HASH", "SOURCE_REFERENCE_POLICY", "OCR_WORKER", "MONGO"], "BLOCKED")
        if not self.settings.source_reference_enabled or not self.settings.source_temporary_files_enabled:
            return result
        result['SOURCE_REFERENCE_POLICY'] = 'PASS'
        if hasattr(self.files, 'readiness'):
            result['FILE_BACKENDS'] = self.files.readiness(probe=True)
        try:
            self.files.check()
            result["FILE_ROOT"] = "PASS"
        except SourceFileError:
            pass
        try:
            page = self.source.version_page(size=3)
            result["DB_SOURCE"] = "PASS"
            if page["items"] and result["FILE_ROOT"] == "PASS":
                matches = []
                for row in page["items"]:
                    reference = self.resolver.import_reference({**row, 'source_system': 'postgresql'})
                    with self.resolver.temporary_path(reference):
                        matches.append(True)
                result["FILE_READ"] = "PASS"
                result["HASH"] = "PASS" if all(matches) else "BLOCKED"
        except Exception:
            pass
        if mongo_client is not None:
            try:
                mongo_client.admin.command("ping")
                result["MONGO"] = "PASS"
            except Exception:
                pass
        # No OCR worker heartbeat registry exists: process readiness cannot be inferred.
        return result
