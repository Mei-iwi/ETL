from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from data_sync_etl.domain.core import DomainError, new_id, now
from data_sync_etl.domain.format_registry import (
    BaseFormatValidator,
    FormatValidatorRegistry,
)
from data_sync_etl.logging import event, flow
from data_sync_etl.ports.processing import StoragePort, StoredFile
from data_sync_etl.ports.repositories import UnitOfWork


@dataclass(frozen=True)
class IngestResourceCommand:
    title: str
    resource_type_code: str
    provider_id: str
    provider_name: str
    filename: str
    file_stream: BinaryIO
    file_size: int
    declared_mime: str = "application/octet-stream"
    provider_type: str = "INTERNAL"
    resource_type_name_vi: str | None = None
    subject_id: str | None = None
    grade_level_id: str | None = None
    publication_status: str = "PUBLISHED"
    uploaded_by: str | None = None


@dataclass(frozen=True)
class IngestResourceResult:
    id: str
    title: str
    resource_type_code: str
    media_category: str
    mime_type: str
    file_name: str
    file_size: int
    source_hash: str
    initial_version_id: str
    version_number: int
    lifecycle_status: str
    created_at: datetime


class IngestResourceUseCase:
    def __init__(
        self,
        uow: UnitOfWork,
        storage: StoragePort,
        registry: FormatValidatorRegistry,
    ) -> None:
        self.uow = uow
        self.storage = storage
        self.registry = registry

    def execute(self, cmd: IngestResourceCommand) -> IngestResourceResult:
        with flow():
            validator = self._resolve_format_validator(cmd)
            self._verify_file_header(cmd, validator)
            stored = self._store_file_data(cmd, validator)
            resource_id, version_id, created_time = self._persist_records(cmd, stored)
            self._log_audit_event(resource_id, version_id)
            return self._build_result(cmd, validator, stored, resource_id, version_id, created_time)

    def _resolve_format_validator(self, cmd: IngestResourceCommand) -> BaseFormatValidator:
        extension = Path(cmd.filename).suffix
        return self.registry.find_validator(extension, cmd.declared_mime)

    def _verify_file_header(
        self, cmd: IngestResourceCommand, validator: BaseFormatValidator
    ) -> None:
        header = self._read_header_bytes(cmd.file_stream)
        extension = Path(cmd.filename).suffix
        validator.validate(
            extension=extension,
            mime_type=cmd.declared_mime,
            header=header,
            file_size=cmd.file_size,
        )

    def _store_file_data(
        self, cmd: IngestResourceCommand, validator: BaseFormatValidator
    ) -> StoredFile:
        return self.storage.save_stream(
            cmd.file_stream, max_size_bytes=validator.max_size_bytes
        )

    def _persist_records(
        self, cmd: IngestResourceCommand, stored: StoredFile
    ) -> tuple[str, str, datetime]:
        timestamp = now()
        resource_id = new_id()
        version_id = new_id()

        with self.uow() as repo:
            self._insert_master_resource(repo, cmd, resource_id, timestamp)
            self._insert_resource_relations(repo, cmd, resource_id)
            self._insert_initial_version(repo, cmd, stored, resource_id, version_id, timestamp)

        return resource_id, version_id, timestamp

    def _insert_master_resource(
        self, repo, cmd: IngestResourceCommand, resource_id: str, timestamp: datetime
    ) -> None:
        type_name = cmd.resource_type_name_vi or cmd.resource_type_code.capitalize()
        repo.insert(
            "master_learning_resources",
            {
                "id": resource_id,
                "created_at": timestamp,
                "updated_at": timestamp,
                "provider_id": cmd.provider_id,
                "provider_name": cmd.provider_name,
                "provider_type": cmd.provider_type,
                "provider_is_active": True,
                "resource_type_id": new_id(),
                "resource_type_code": cmd.resource_type_code,
                "resource_type_name_vi": type_name,
                "resource_type_is_active": True,
                "created_by": cmd.uploaded_by,
                "title": cmd.title,
                "publication_status": cmd.publication_status,
                "is_active": True,
            },
        )

    def _insert_resource_relations(
        self, repo, cmd: IngestResourceCommand, resource_id: str
    ) -> None:
        if cmd.subject_id:
            subject = repo.get("subjects", cmd.subject_id)
            if not subject:
                raise DomainError(f"Subject not found: '{cmd.subject_id}'")
            repo.insert(
                "resource_subjects",
                {"resource_id": resource_id, "subject_id": cmd.subject_id, "is_primary": True},
            )
        if cmd.grade_level_id:
            grade = repo.get("master_grade_levels", cmd.grade_level_id)
            if not grade:
                raise DomainError(f"Grade level not found: '{cmd.grade_level_id}'")
            repo.insert(
                "resource_grade_levels",
                {"resource_id": resource_id, "grade_level_id": cmd.grade_level_id},
            )

    def _insert_initial_version(
        self,
        repo,
        cmd: IngestResourceCommand,
        stored: StoredFile,
        resource_id: str,
        version_id: str,
        timestamp: datetime,
    ) -> None:
        repo.insert(
            "resource_versions",
            {
                "id": version_id,
                "created_at": timestamp,
                "updated_at": timestamp,
                "resource_id": resource_id,
                "parent_version_id": None,
                "version_number": 1,
                "lifecycle_status": "INGESTED",
                "storage_bucket": stored.bucket,
                "storage_object_key": stored.object_key,
                "source_hash": stored.source_hash,
                "uploaded_by": cmd.uploaded_by,
            },
        )

    @staticmethod
    def _read_header_bytes(stream: BinaryIO, size: int = 512) -> bytes:
        header = stream.read(size)
        stream.seek(0)
        return header

    @staticmethod
    def _log_audit_event(resource_id: str, version_id: str) -> None:
        event(
            resource_id=resource_id,
            resource_version_id=version_id,
            stage="ingest",
            action="RESOURCE_CREATED",
        )

    @staticmethod
    def _build_result(
        cmd: IngestResourceCommand,
        validator: BaseFormatValidator,
        stored: StoredFile,
        resource_id: str,
        version_id: str,
        created_time: datetime,
    ) -> IngestResourceResult:
        return IngestResourceResult(
            id=resource_id,
            title=cmd.title,
            resource_type_code=cmd.resource_type_code,
            media_category=validator.category.value,
            mime_type=cmd.declared_mime,
            file_name=cmd.filename,
            file_size=cmd.file_size,
            source_hash=stored.source_hash,
            initial_version_id=version_id,
            version_number=1,
            lifecycle_status="INGESTED",
            created_at=created_time,
        )
