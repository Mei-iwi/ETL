"""Hệ thống Đối soát & Kiểm tra Toàn vẹn Dữ liệu (Data Reconciliation Engine).

Kiến trúc kỹ thuật:
1. Kiểm tra dung lượng file 2 cấp: Fast Check O(1) qua os.stat + Deep Check O(N) stream SHA-256.
2. Tóm tắt dữ liệu đa tầng: Đối soát vĩ mô (COUNT, MIN/MAX ID, MAX updated_at) +
   Mã băm gộp bảng (Aggregate Fingerprint) chạy trực tiếp bằng hàm băm SQL trong PostgreSQL.
3. Ma trận cờ trạng thái đồng bộ:
   - SYNCED: Đồng nhất 100%
   - DESYNC_METADATA: Lệch trường metadata giữa Nguồn và Đích
   - DESYNC_BLOB_SIZE: Sai lệch dung lượng file vật lý
   - DESYNC_CHECKSUM: Sai lệch mã băm nội dung tệp (Silent Corruption)
   - ORPHAN_SOURCE: Bản ghi có ở Nguồn nhưng thiếu ở Master
   - ORPHAN_MASTER: Bản ghi có ở Master nhưng không còn ở Nguồn
   - MUTATED_SOURCE: Nguồn bị thay đổi sau lần sync trước
   - MUTATED_DOWNSTREAM: Master bị sửa đổi ngoài luồng ETL
4. Ngưỡng an toàn tự phục hồi (Safety bounds):
   Tỷ lệ lệch tối đa max_ratio <= 10% (0.1) ngăn chặn lỗi xóa/sửa hàng loạt.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter
from copy import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from typing import Any

from sqlalchemy import inspect, or_, select, text
from sqlalchemy.orm import Session

from data_sync_etl.adapters.persistence.audit_io import MeteredFiles
from data_sync_etl.db.models import (
    MODELS,
    MasterLearningResource,
    ResourceVersion,
    SyncReconciliationDiscrepancy,
    SyncReconciliationRun,
)
from data_sync_etl.domain.core import DomainError, SourceFileError, new_id, now
from data_sync_etl.domain.reconciliation import (
    FLAG_DESYNC_BLOB_SIZE,
    FLAG_DESYNC_CHECKSUM,
    FLAG_DESYNC_METADATA,
    FLAG_MUTATED_DOWNSTREAM,
    FLAG_MUTATED_SOURCE,
    FLAG_NOT_VERIFIED,
    FLAG_ORPHAN_MASTER,
    FLAG_ORPHAN_SOURCE,
    FLAG_SYNCED,
    METADATA_FIELDS,
    STREAM_TABLE_MAPPING,
    metadata_differences,
    serialize_json_value,
)
from data_sync_etl.domain.source_ownership import state_hash

# Ma trận cờ trạng thái đối soát


class _ReadBudget:
    """Cooperative cancellation for existing provider readers; no timer thread."""
    def __init__(self, deadline, stopped=None):
        self.deadline, self.stopped = deadline, stopped

    def is_set(self):
        return monotonic() >= self.deadline or bool(self.stopped and self.stopped.is_set())

    def wait(self, seconds):
        (self.stopped or Event()).wait(max(0, min(seconds, self.deadline - monotonic())))
        return self.is_set()








class ReconciliationEngine:
    """Động cơ đối soát và kiểm tra toàn vẹn dữ liệu tự động giữa Source và Master."""

    def __init__(
        self,
        uow,
        source_adapter=None,
        storage_root: Path | str = "storage",
        max_ratio: float = 0.1,
        batch_size: int = 500,
    ):
        self.uow = uow
        self.source_adapter = source_adapter
        self.storage_root = Path(storage_root)
        self.max_ratio = max_ratio
        self.batch_size = batch_size
        self._run_lock = Lock()
        self.source_resolver = None
        self.file_read_budget_seconds = 30
        self.file_cancelled = None
        self.file_max_bytes = 2147483648
        self.file_metrics = {}

    def compute_table_summary(
        self,
        session_or_conn: Any,
        table_name: str,
        stream_name: str,
    ) -> dict[str, Any]:
        """Tính toán đối soát vĩ mô và mã băm bảng (Table Fingerprint) trực tiếp trên PostgreSQL.

        1. Macro audit: COUNT(*), MIN(id), MAX(id), MAX(updated_at).
        2. Micro aggregate hash: MD5(STRING_AGG(MD5(id:updated_at), '' ORDER BY id)).
        Hỗ trợ tương thích an toàn cả PostgreSQL (server-side hashing) và SQLite (in-memory test).
        """
        # Xác định dialect của kết nối
        bind = getattr(session_or_conn, "bind", None) or session_or_conn
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "postgresql")

        updated_col = "grade_updated_at" if table_name == "master_grade_levels" else "updated_at"

        if dialect_name == "postgresql":
            sql = f"""
            SELECT
                count(*)::integer AS total_count,
                coalesce(min(id::text), '') AS min_id,
                coalesce(max(id::text), '') AS max_id,
                max({updated_col}) AS max_updated_at,
                coalesce(
                    md5(string_agg(
                        md5(id::text || ':' || coalesce(to_char({updated_col} at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'), '')),
                        '' ORDER BY id::text
                    )),
                    ''
                ) AS table_fingerprint
            FROM {table_name}
            """
            row = session_or_conn.execute(text(sql)).mappings().one()
            max_dt = row["max_updated_at"]
            if isinstance(max_dt, datetime) and max_dt.tzinfo is None:
                max_dt = max_dt.replace(tzinfo=UTC)

            # Tính tổng byte nếu bảng hỗ trợ kích thước tệp
            total_bytes = 0
            if table_name in ("resource_versions", "source_file_imports"):
                col = "source_file_size" if "source" in table_name else "storage_file_size"
                try:
                    bytes_sql = f"SELECT coalesce(sum(coalesce({col}, 0)), 0)::bigint FROM {table_name}"
                    total_bytes = session_or_conn.execute(text(bytes_sql)).scalar() or 0
                except Exception:
                    total_bytes = 0

            return {
                "count": row["total_count"],
                "min_id": row["min_id"],
                "max_id": row["max_id"],
                "max_updated_at": max_dt.isoformat() if max_dt else None,
                "table_fingerprint": row["table_fingerprint"],
                "total_bytes": total_bytes,
            }
        else:
            # Fallback tương thích môi trường SQLite unit test
            sql = f"SELECT count(*), min(id), max(id), max({updated_col}) FROM {table_name}"
            row = session_or_conn.execute(text(sql)).one()
            count, min_id, max_id, max_updated = row[0], row[1] or "", row[2] or "", row[3]

            rows_sql = f"SELECT id, {updated_col} FROM {table_name} ORDER BY id"
            items = session_or_conn.execute(text(rows_sql)).all()
            agg = hashlib.md5()
            for r_id, r_up in items:
                up_str = str(r_up) if r_up is not None else ""
                row_hash = hashlib.md5(f"{r_id}:{up_str}".encode()).hexdigest()
                agg.update(row_hash.encode())

            fingerprint = agg.hexdigest() if count > 0 else ""
            return {
                "count": count,
                "min_id": min_id,
                "max_id": max_id,
                "max_updated_at": str(max_updated) if max_updated else None,
                "table_fingerprint": fingerprint,
                "total_bytes": 0,
            }

    def verify_file_blobs(
        self,
        db_session: Session,
        storage_root: Path | None = None,
        sample_limit: int | None = None,
        version_ids: list[str] | None = None,
        mode: str = "INCREMENTAL",
        since: datetime | None = None,
        audit_type: str = "DEEP",
    ) -> list[dict[str, Any]]:
        """Kiểm tra toàn vẹn dung lượng file vật lý 2 cấp:

        Cấp 1 (Fast Check): So sánh metadata bytes source_file_size == storage_file_size qua os.stat O(1).
        Cấp 2 (Deep Check): Đọc stream SHA-256 đối chiếu source_hash O(N) khi kích thước khớp (có Smart Cache).
        """
        if audit_type not in ('LIGHT', 'DEEP'):
            raise DomainError('INVALID_AUDIT_TYPE')
        self.file_metrics = {'bytes_read': 0, 'sha256_computed_count': 0, 'cache_reused_count': 0}
        root = Path(storage_root or self.storage_root)
        stmt = select(ResourceVersion).order_by(ResourceVersion.id).limit(10001)
        if version_ids is not None:
            stmt = stmt.where(ResourceVersion.id.in_(version_ids))
        elif mode == "INCREMENTAL" and since is not None:
            stmt = stmt.where(
                or_(
                    ResourceVersion.updated_at >= since,
                    ResourceVersion.storage_file_size.is_(None),
                    ResourceVersion.storage_file_size <= 0,
                )
            )
        if sample_limit:
            stmt = stmt.limit(sample_limit)

        versions = db_session.scalars(stmt).all()
        if len(versions) > 10000:
            raise DomainError('RECONCILIATION_LIMIT_EXCEEDED')
        origins = dict(db_session.execute(select(MasterLearningResource.id, MasterLearningResource.source_origin)
            .where(MasterLearningResource.id.in_({row.resource_id for row in versions}))).all())
        return self.verify_file_snapshot(versions, origins, root, audit_type)

    def verify_file_snapshot(self, versions, origins, root=None, audit_type='DEEP'):
        """Verify detached audit metadata, allowing callers to release DB transactions."""
        root = Path(root or self.storage_root)
        self.file_metrics = {'bytes_read': 0, 'sha256_computed_count': 0, 'cache_reused_count': 0}
        results: list[dict[str, Any]] = []

        for ver in versions:
            # A slow first file must not consume the deadline of every later file.
            deadline = monotonic() + self.file_read_budget_seconds
            # Ownership comes from the resource, never an assumed bucket name.
            # SOURCE bytes are not verified by inspecting historical Master copies.
            bucket = ver.storage_bucket or ""
            is_source_reference = origins.get(ver.resource_id) != 'LOCAL'
            if is_source_reference:
                result = {
                    "record_id": ver.id,
                    "discrepancy_flag": FLAG_NOT_VERIFIED,
                    "source_value": {
                        "version_number": ver.version_number,
                        "source_file_size": ver.source_file_size,
                    },
                    "target_value": {
                        "storage_file_size": ver.storage_file_size,
                        "note": "SOURCE_REFERENCE_ONLY — blob không lưu trong storage local",
                    },
                    "message": "Chưa xác minh được tệp nguồn",
                }
                try:
                    if monotonic() >= deadline:
                        raise SourceFileError('RECONCILIATION_READ_BUDGET')
                    if self.source_resolver is None:
                        raise SourceFileError('SOURCE_RESOLVER_UNAVAILABLE')
                    reference = self.source_resolver.resolve(ver.id)
                    if reference.ownership != 'SOURCE_BACKED':
                        raise SourceFileError('BLOCKED_SOURCE_MAPPING')
                    if audit_type == 'LIGHT':
                        revision = self.source_resolver.files.revision(reference.bucket, reference.opaque_locator,
                            cancelled=_ReadBudget(deadline, self.file_cancelled))
                        if reference.provider not in ('local', 'google_drive') or not isinstance(revision, tuple) or len(revision) != 5:
                            raise SourceFileError('SIZE_METADATA_UNSUPPORTED')
                        size = int(revision[2])
                        expected = ver.source_file_size
                        mismatch = expected is not None and expected != size
                        result['discrepancy_flag'] = FLAG_DESYNC_BLOB_SIZE if mismatch else FLAG_NOT_VERIFIED
                        result['target_value'] = {'storage_file_size': size, 'verification': 'SIZE_ONLY',
                            'observed_revision': hashlib.sha256(repr(revision).encode()).hexdigest(),
                            'size_status': 'SIZE_MISMATCH' if mismatch else 'SIZE_MATCHED' if expected is not None else 'NOT_VERIFIED',
                            'reason_code': 'SHA256_NOT_CHECKED'}
                        results.append(result)
                        continue
                    # Resolver verifies canonical ownership, source revision and SHA-256,
                    # then removes the private temporary file. Never inspect a Master copy.
                    resolver = copy(self.source_resolver)
                    if hasattr(resolver, 'files'):
                        resolver.files = MeteredFiles(resolver.files, self.file_metrics, self.file_max_bytes,
                                                      reference.provider)
                    with resolver.temporary_path(reference, cancelled=_ReadBudget(deadline, self.file_cancelled)) as source_path:
                        size = source_path.stat().st_size
                    self.file_metrics['sha256_computed_count'] += 1
                    expected = ver.source_file_size
                    flag = FLAG_DESYNC_BLOB_SIZE if expected is not None and expected != size else FLAG_SYNCED
                    result.update(discrepancy_flag=flag, message='Đã đọc và xác minh tệp từ nguồn')
                    result['target_value'] = {'verified_size': size, 'verified_sha256': ver.source_hash,
                                              'verification': 'SOURCE_STREAM_SHA256'}
                except SourceFileError as exc:
                    if exc.code == 'SOURCE_HASH_MISMATCH':
                        self.file_metrics['sha256_computed_count'] += 1
                    flag = {'SOURCE_HASH_MISMATCH': FLAG_DESYNC_CHECKSUM,
                            'SOURCE_VERSION_MUTATED': FLAG_MUTATED_SOURCE}.get(exc.code, FLAG_NOT_VERIFIED)
                    result.update(discrepancy_flag=flag)
                    result['target_value'] = {'verification': 'NOT_VERIFIED', 'reason_code':
                        'RECONCILIATION_READ_BUDGET' if exc.code == 'SOURCE_REQUEST_CANCELLED' else exc.code}
                except OSError:
                    result['target_value'] = {'verification': 'NOT_VERIFIED', 'reason_code': 'SOURCE_UNAVAILABLE'}
                results.append(result)
                continue

            # Xác định vị trí tệp LOCAL_OWNED trên kho lưu trữ
            blob_path = (root / bucket / ver.storage_object_key).resolve()
            if not blob_path.is_relative_to(root.resolve()):
                results.append({'record_id': ver.id, 'discrepancy_flag': FLAG_DESYNC_BLOB_SIZE,
                                'source_value': None, 'target_value': {'file_exists': False},
                                'message': 'Unsafe storage path'})
                continue
            if not blob_path.is_file():
                # Thử đường dẫn không có bucket nếu object_key đã chứa đầy đủ
                alt_path = root / ver.storage_object_key
                if alt_path.resolve().is_relative_to(root.resolve()) and alt_path.is_file():
                    blob_path = alt_path

            if not blob_path.is_file():
                # Tệp vật lý bị thiếu hoàn toàn trên đĩa
                results.append({
                    "record_id": ver.id,
                    "discrepancy_flag": FLAG_DESYNC_BLOB_SIZE,
                    "source_value": {
                        "version_number": ver.version_number,
                        "source_file_size": ver.source_file_size,
                        "source_hash": ver.source_hash,
                    },
                    "target_value": {
                        "storage_file_size": 0,
                        "file_exists": False,
                        "size_status": "FILE_MISSING",
                    },
                    "message": "Physical file blob missing on disk",
                })
                continue

            # Cấp 1: Fast Check dung lượng (O(1))
            try:
                observed_stat = blob_path.stat()
                st_size = observed_stat.st_size
            except OSError:
                results.append({'record_id': ver.id, 'discrepancy_flag': FLAG_NOT_VERIFIED,
                    'source_value': None, 'target_value': {'reason_code': 'LOCAL_FILE_UNAVAILABLE'}})
                continue
            expected_size = ver.source_file_size

            if expected_size is not None and expected_size != st_size:
                # Kích thước byte bị lệch
                results.append({
                    "record_id": ver.id,
                    "discrepancy_flag": FLAG_DESYNC_BLOB_SIZE,
                    "source_value": {
                        "version_number": ver.version_number,
                        "source_file_size": expected_size,
                        "source_hash": ver.source_hash,
                    },
                    "target_value": {
                        "storage_file_size": st_size,
                        "file_exists": True,
                        "size_delta_bytes": st_size - expected_size,
                    },
                    "message": f"File size mismatch: source={expected_size} bytes, storage={st_size} bytes",
                })
                continue

            # Cấp 2: Deep Check SHA-256 (O(N)) qua stream buffer 64KB kèm Smart Cache
            if audit_type == 'LIGHT':
                results.append({'record_id': ver.id, 'discrepancy_flag': FLAG_NOT_VERIFIED,
                    'source_value': {'source_file_size': expected_size},
                    'target_value': {'storage_file_size': st_size, 'verification': 'SIZE_ONLY',
                        'observed_revision': hashlib.sha256(repr((observed_stat.st_dev, observed_stat.st_ino,
                            observed_stat.st_size, observed_stat.st_mtime_ns, observed_stat.st_ctime_ns)).encode()).hexdigest(),
                        'size_status': 'SIZE_MATCHED' if expected_size is not None else 'NOT_VERIFIED',
                        'reason_code': 'SHA256_NOT_CHECKED'}})
                continue
            if not re.fullmatch('[0-9a-fA-F]{64}', ver.source_hash or ''):
                results.append({'record_id': ver.id, 'discrepancy_flag': FLAG_NOT_VERIFIED,
                    'source_value': None, 'target_value': {'reason_code': 'EXPECTED_SHA256_UNAVAILABLE'}})
                continue
            try:
                computed_hash = self._hash_local(blob_path, deadline)
            except (OSError, SourceFileError) as exc:
                results.append({'record_id': ver.id, 'discrepancy_flag': FLAG_NOT_VERIFIED,
                    'source_value': None, 'target_value': {'verification': 'NOT_VERIFIED',
                        'reason_code': exc.code if isinstance(exc, SourceFileError) else 'LOCAL_FILE_UNAVAILABLE'}})
                continue

            if computed_hash != ver.source_hash.lower():
                # Dung lượng khớp nhưng nội dung bị hỏng / sửa đổi ngầm (Silent corruption)
                results.append({
                    "record_id": ver.id,
                    "discrepancy_flag": FLAG_DESYNC_CHECKSUM,
                    "source_value": {
                        "version_number": ver.version_number,
                        "source_hash": ver.source_hash,
                        "source_file_size": expected_size or st_size,
                    },
                    "target_value": {
                        "computed_hash": computed_hash,
                        "storage_file_size": st_size,
                    },
                    "message": f"SHA-256 mismatch: expected={ver.source_hash}, actual={computed_hash}",
                })
            else:
                # Hoàn toàn đồng nhất
                results.append({
                    "record_id": ver.id,
                    "discrepancy_flag": FLAG_SYNCED,
                    "source_value": {
                        "version_number": ver.version_number,
                        "source_hash": ver.source_hash,
                        "source_file_size": st_size,
                    },
                    "target_value": {
                        "source_hash": computed_hash,
                        "storage_file_size": st_size,
                    },
                    "message": "File blob verified and synced",
                })

        return results

    def _hash_local(self, path, deadline):
        import os

        def revision(value):
            return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)

        before = path.stat()
        if before.st_size > self.file_max_bytes - self.file_metrics['bytes_read']:
            raise SourceFileError('RECONCILIATION_BYTE_BUDGET')
        sha = hashlib.sha256()
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            # Windows stat/fstat can expose different ctime semantics. Compare
            # each ctime against the same API, and identity across both APIs.
            if revision(before)[:4] != revision(opened)[:4]:
                raise SourceFileError('LOCAL_FILE_CHANGED')
            while True:
                if _ReadBudget(deadline, self.file_cancelled).is_set():
                    raise SourceFileError('RECONCILIATION_READ_BUDGET')
                remaining = self.file_max_bytes - self.file_metrics['bytes_read']
                if remaining <= 0:
                    if stream.tell() == before.st_size and revision(before) == revision(path.stat()):
                        break
                    raise SourceFileError('RECONCILIATION_BYTE_BUDGET')
                block = stream.read(min(65536, remaining))
                self.file_metrics['bytes_read'] += len(block)
                if not block:
                    break
                sha.update(block)
            if revision(opened) != revision(os.fstat(stream.fileno())) or revision(before) != revision(path.stat()):
                raise SourceFileError('LOCAL_FILE_CHANGED')
        self.file_metrics['sha256_computed_count'] += 1
        return sha.hexdigest()

    def _fetch_source_records(
        self, stream_name: str, mode: str = "INCREMENTAL", since: datetime | None = None
    ) -> dict[str, dict[str, Any]]:
        """Lấy snapshot bản ghi từ Source theo stream. Hỗ trợ INCREMENTAL."""
        if self.source_adapter is None:
            raise DomainError('SOURCE_UNAVAILABLE')

        source_records: dict[str, dict[str, Any]] = {}
        # Nếu là PostgreSQLSourceAdapter có engine
        if hasattr(self.source_adapter, "engine"):
            tbl = STREAM_TABLE_MAPPING.get(stream_name, {}).get("source_table", stream_name)
            where_sql = ""
            params: dict[str, Any] = {}
            if mode == "INCREMENTAL" and since is not None:
                where_sql = " WHERE updated_at >= :since"
                params["since"] = since

            with self.source_adapter.engine.connect() as conn:
                if conn.dialect.name == 'postgresql':
                    conn.exec_driver_sql('SET TRANSACTION READ ONLY')
                    conn.exec_driver_sql("SET LOCAL statement_timeout = '5000'")
                aliases = ({'code': 'grade_code', 'name_vi': 'grade_name_vi',
                            'updated_at': 'grade_updated_at',
                            'is_active': 'grade_is_active', 'deleted_at': 'grade_deleted_at'}
                           if stream_name == 'grades' else {})
                available = {c['name'] for c in inspect(conn).get_columns(tbl)}
                columns = ['id', 'updated_at'] + [c for c in available
                    if aliases.get(c, c) in METADATA_FIELDS.get(stream_name, ())]
                projection = ', '.join(f'{c} AS {aliases[c]}' if c in aliases else c
                                       for c in dict.fromkeys(columns))
                key = 'id COLLATE "C"' if conn.dialect.name == 'postgresql' else 'id'
                after = None
                while True:
                    conditions = (['updated_at >= :since'] if where_sql else [])
                    if after is not None:
                        conditions.append(f'{key} > :after')
                    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
                    rows = conn.execute(text(f'SELECT {projection} FROM {tbl}{where} ORDER BY {key} LIMIT :size'),
                        {**params, 'after': after, 'size': self.batch_size}).mappings().all()
                    if not rows:
                        break
                    for r in rows:
                        source_records[str(r['id'])] = dict(r)
                    if len(source_records) > 10000:
                        raise DomainError('RECONCILIATION_LIMIT_EXCEEDED')
                    after = rows[-1]['id']
            return source_records

        # Nếu là adapter hỗ trợ snapshot context manager (ReconciliationSource)
        if hasattr(self.source_adapter, "snapshot"):
            with self.source_adapter.snapshot() as reader:
                after = None
                while True:
                    ids = reader.reconciliation_ids(stream_name, after, self.batch_size)
                    if not ids:
                        break
                    for r_id in ids:
                        source_records[str(r_id)] = {"id": str(r_id)}
                    if len(source_records) > 10000:
                        raise DomainError('RECONCILIATION_LIMIT_EXCEEDED')
                    after = ids[-1]
            return source_records

        # Fallback: nếu adapter có phương thức batches
        if hasattr(self.source_adapter, "batches"):
            for batch in self.source_adapter.batches(stream_name, cursor=None, batch_size=self.batch_size):
                for agg in batch:
                    r_id = getattr(agg, "id", None) or agg.values.get("id")
                    if since is None or agg.cursor.updated_at >= since:
                        source_records[str(r_id)] = dict(agg.values)
                    if len(source_records) > 10000:
                        raise DomainError('RECONCILIATION_LIMIT_EXCEEDED')
            return source_records

        return source_records

    def execute_stream_reconcile(
        self, stream_name: str = 'all', dry_run: bool = True, mode: str = "INCREMENTAL",
    ) -> dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            raise DomainError('RECONCILIATION_BUSY')
        try:
            return self._execute_stream_reconcile(stream_name, dry_run, mode=mode)
        finally:
            self._run_lock.release()

    def _execute_stream_reconcile(
        self,
        stream_name: str = "all",
        dry_run: bool = True,
        mode: str = "INCREMENTAL",
    ) -> dict[str, Any]:
        """Thực thi đối soát toàn diện giữa Nguồn và Đích.

        - Kiểm tra đối soát vĩ mô & micro table fingerprint.
        - Phát hiện và phân loại các cờ: ORPHAN_SOURCE, ORPHAN_MASTER, DESYNC_METADATA, DESYNC_BLOB_SIZE, DESYNC_CHECKSUM.
        - Kiểm tra ngưỡng an toàn max_ratio <= 10%: Nếu tỷ lệ lệch vượt ngưỡng -> BLOCKED_MASS_DISCREPANCY.
        - Ghi kết quả vào sync_reconciliation_runs và sync_reconciliation_discrepancies.
        """
        t_start_total = time.perf_counter()
        if not dry_run:
            raise DomainError('RECONCILIATION_READ_ONLY')
        streams_to_run = (
            ["grades", "subjects", "users", "resources", "files"]
            if stream_name == "all"
            else [stream_name]
        )

        overall_reports: list[dict[str, Any]] = []

        with self.uow() as repo:
            session: Session = repo.session

            for stream in streams_to_run:
                source_dict, target_rows, file_results = {}, [], []
                unverified_metadata, matched_metadata = 0, 0
                mapping = STREAM_TABLE_MAPPING.get(stream)
                if not mapping:
                    continue

                source_tbl = mapping["source_table"]
                target_tbl = mapping["target_table"]
                run_id = new_id()
                started = now()
                stream_start = time.perf_counter()

                # 1. Tính toán đối soát vĩ mô cho Đích (Master)
                target_summary = (self.compute_table_summary(session, target_tbl, stream)
                                  if mode == 'FULL' else {})

                # 2. Tính toán đối soát vĩ mô cho Nguồn (Source)
                source_summary: dict[str, Any] = {"count": 0, "table_fingerprint": None, "total_bytes": 0}
                if mode == 'FULL' and hasattr(self.source_adapter, "engine"):
                    with self.source_adapter.engine.connect() as s_conn:
                        source_summary = self.compute_table_summary(s_conn, source_tbl, stream)
                elif mode == 'FULL' and hasattr(self.source_adapter, "snapshot"):
                    with self.source_adapter.snapshot() as reader:
                        s_meta = reader.reconciliation_summary(stream)
                        source_summary = {
                            "count": s_meta.get("count", 0),
                            "min_id": s_meta.get("minimum", ""),
                            "max_id": s_meta.get("maximum", ""),
                            "table_fingerprint": None,
                            "total_bytes": 0,
                        }

                # Xác định mốc thời gian since nếu là INCREMENTAL
                since = None
                if mode == "INCREMENTAL":
                    last_run = session.scalar(
                        select(SyncReconciliationRun)
                        .where(
                            SyncReconciliationRun.stream_name == stream,
                            SyncReconciliationRun.status == "COMPLETED",
                        )
                        .order_by(SyncReconciliationRun.started_at.desc())
                        .limit(1)
                    )
                    if last_run and last_run.started_at:
                        since = last_run.started_at - timedelta(seconds=300)

                # 3. Quét chi tiết từng bản ghi để so sánh song song
                discrepancies: list[dict[str, Any]] = []

                if stream == "files":
                    # Luồng tệp học liệu: thực hiện Fast Check và Deep Check trên Storage
                    file_results = self.verify_file_blobs(session, self.storage_root, mode=mode, since=since)
                    for fr in file_results:
                        if fr["discrepancy_flag"] != FLAG_SYNCED:
                            discrepancies.append({
                                "record_id": fr["record_id"],
                                "stream_name": stream,
                                "discrepancy_flag": fr["discrepancy_flag"],
                                "source_value": fr["source_value"],
                                "target_value": fr["target_value"],
                            })
                else:
                    # Luồng metadata: lấy dữ liệu 2 bên
                    source_dict = self._fetch_source_records(stream, mode=mode, since=since)
                    if mode == 'INCREMENTAL' and since is not None:
                        model = MODELS[target_tbl]
                        target_rows = []
                        ids = list(source_dict)
                        columns = [c for c in model.__table__.columns if not c.info.get('sensitive')]
                        for start in range(0, len(ids), self.batch_size):
                            target_rows.extend({key: value.replace(tzinfo=UTC)
                                if isinstance(value, datetime) and value.tzinfo is None else value
                                for key, value in row.items()} for row in session.execute(
                                select(*columns).where(model.id.in_(ids[start:start + self.batch_size]))
                            ).mappings())
                    else:
                        target_rows = repo.find(target_tbl, limit=10001)
                    if len(target_rows) > 10000:
                        raise DomainError('RECONCILIATION_LIMIT_EXCEEDED')
                    target_dict = {str(r["id"]): r for r in target_rows}

                    all_ids = set(source_dict.keys()) | set(target_dict.keys())

                    for rec_id in sorted(all_ids):
                        s_val = source_dict.get(rec_id)
                        t_val = target_dict.get(rec_id)
                        if t_val and t_val.get('source_origin') == 'LOCAL':
                            continue

                        if s_val and not t_val:
                            # Bản ghi có ở Nguồn nhưng thiếu ở Master
                            discrepancies.append({
                                "record_id": rec_id,
                                "stream_name": stream,
                                "discrepancy_flag": FLAG_ORPHAN_SOURCE,
                                "source_value": s_val,
                                "target_value": None,
                            })
                        elif t_val and not s_val:
                            # Bản ghi có ở Master nhưng không còn ở Nguồn
                            origin = t_val.get("source_origin", "POSTGRESQL")
                            # Absence requires authoritative coverage, never a delta or unknown ownership.
                            flag = FLAG_NOT_VERIFIED
                            if origin == 'POSTGRESQL' and (mode == 'FULL' or since is None):
                                adapter = self.source_adapter
                                if hasattr(adapter, 'snapshot'):
                                    with adapter.snapshot() as reader:
                                        reader.reconciliation_summary(stream)
                                        if rec_id not in reader.reconciliation_present(stream, [rec_id]):
                                            flag = FLAG_ORPHAN_MASTER
                            unverified_metadata += flag == FLAG_NOT_VERIFIED
                            discrepancies.append({
                                "record_id": rec_id,
                                "stream_name": stream,
                                "discrepancy_flag": flag,
                                "source_value": None,
                                "target_value": t_val,
                            })
                        elif s_val and t_val:
                            # Cả 2 bên đều có: phát hiện Master bị sửa ngầm downstream.
                            # Logic: so sánh source_state_hash lưu trong Master với hash thực tế
                            # của bản ghi Master (state_hash tự loại source_origin/source_state_hash/source_missing_at).
                            # Nếu khác → Master bị sửa ngoài luồng ETL (MUTATED_DOWNSTREAM).
                            db_hash = t_val.get("source_state_hash")
                            actual_hash = state_hash(t_val)
                            if db_hash and db_hash != actual_hash:
                                discrepancies.append({
                                    "record_id": rec_id,
                                    "stream_name": stream,
                                    "discrepancy_flag": FLAG_MUTATED_DOWNSTREAM,
                                    "source_value": s_val,
                                    "target_value": t_val,
                                })
                            else:
                                compared = [key for key in METADATA_FIELDS.get(stream, ()) if key in s_val and key in t_val]
                                missing = set(METADATA_FIELDS.get(stream, ())) - set(compared)
                                unverified_metadata += bool(missing)
                                different = metadata_differences(stream, s_val, t_val)
                                if different:
                                    discrepancies.append({'record_id': rec_id, 'stream_name': stream,
                                        'discrepancy_flag': FLAG_DESYNC_METADATA,
                                        'source_value': {**{key: s_val[key] for key in different}, 'different_fields': different},
                                        'target_value': {**{key: t_val[key] for key in different}, 'different_fields': different}})
                                elif compared and not missing:
                                    matched_metadata += 1
                                elif missing:
                                    discrepancies.append({'record_id': rec_id, 'stream_name': stream,
                                        'discrepancy_flag': FLAG_NOT_VERIFIED, 'source_value': None,
                                        'target_value': {'reason_code': 'METADATA_UNSUPPORTED',
                                                         'missing_fields': sorted(missing)}})

                # 4. Đánh giá ngưỡng an toàn tự phục hồi (Safety bounds check)
                s_count = source_summary.get("count", len(source_dict))
                t_count = target_summary.get("count", len(target_rows))
                if stream != 'files':
                    s_count = len(source_dict) - sum(row.get('source_origin') == 'LOCAL'
                        and str(row['id']) in source_dict for row in target_rows)
                    t_count = sum(row.get('source_origin') != 'LOCAL' for row in target_rows)
                else:
                    s_count, t_count = 0, len(file_results)
                if mode == 'INCREMENTAL':
                    source_summary = {'count': s_count, 'scope': 'SOURCE_DELTA' if since else 'INITIAL_AUDIT'}
                    target_summary = {'count': t_count, 'scope': 'INSPECTED_RECORDS'}
                mismatched_count = sum(row['discrepancy_flag'] != FLAG_NOT_VERIFIED for row in discrepancies)
                skipped_files = sum(row['discrepancy_flag'] == FLAG_NOT_VERIFIED for row in file_results)
                max_population = max(1, max(s_count, t_count))
                matched_count = (sum(row['discrepancy_flag'] == FLAG_SYNCED for row in file_results)
                                 if stream == 'files' else matched_metadata)
                mismatch_ratio = mismatched_count / max_population

                summary_meta = {
                    "source_summary": source_summary,
                    "target_summary": target_summary,
                    "dry_run": dry_run,
                    "verification_scope": ('MASTER_REFERENCED_VERSIONS' if stream == 'files'
                        else 'SOURCE_DELTA' if since is not None else 'FULL_METADATA'),
                    "whole_repository_verified": False,
                    "watermark_since": since.isoformat() if since else None,
                    "mismatch_ratio": round(mismatch_ratio, 4),
                    "max_allowed_ratio": self.max_ratio,
                    "unverified_source_files": skipped_files,
                    "unverified_metadata_records": unverified_metadata,
                    "verified_file_count": sum(row['discrepancy_flag'] == FLAG_SYNCED for row in file_results),
                    "verification_reason_counts": dict(Counter(
                        row['target_value'].get('reason_code', 'UNKNOWN') for row in file_results
                        if row['discrepancy_flag'] == FLAG_NOT_VERIFIED)),
                }

                if mismatch_ratio > self.max_ratio:
                    run_status = "BLOCKED"
                    summary_meta["guard_alert"] = "BLOCKED_MASS_DISCREPANCY"
                    summary_meta["reason"] = (
                        f"Tỷ lệ lệch ({round(mismatch_ratio * 100, 2)}%) vượt ngưỡng an toàn "
                        f"cho phép ({self.max_ratio * 100}%). Cần kiểm tra trước khi đồng bộ lại."
                    )
                else:
                    run_status = "PARTIAL" if skipped_files or unverified_metadata else "COMPLETED"

                finished = now()
                stream_elapsed_ms = round((time.perf_counter() - stream_start) * 1000, 2)
                summary_meta["mode"] = mode
                summary_meta["execution_time_ms"] = stream_elapsed_ms

                # 5. Lưu kết quả chạy vào sync_reconciliation_runs
                run_record = SyncReconciliationRun(
                    run_id=run_id,
                    stream_name=stream,
                    started_at=started,
                    finished_at=finished,
                    status=run_status,
                    source_count=s_count,
                    target_count=t_count,
                    matched_count=matched_count,
                    mismatched_count=mismatched_count,
                    source_total_bytes=source_summary.get("total_bytes", 0),
                    target_total_bytes=target_summary.get("total_bytes", 0),
                    source_table_hash=source_summary.get("table_fingerprint"),
                    target_table_hash=target_summary.get("table_fingerprint"),
                    summary_meta=serialize_json_value(summary_meta),
                    created_at=started,
                )
                session.add(run_record)
                session.flush()

                # 6. Lưu chi tiết các bản ghi lệch vào sync_reconciliation_discrepancies
                for disc in discrepancies:
                    discrepancy_row = SyncReconciliationDiscrepancy(
                        id=new_id(),
                        run_id=run_id,
                        record_id=disc["record_id"],
                        stream_name=stream,
                        discrepancy_flag=disc["discrepancy_flag"],
                        source_value=serialize_json_value(disc.get("source_value")),
                        target_value=serialize_json_value(disc.get("target_value")),
                        detected_at=started,
                        resolution_status=disc.get("resolution_status", "UNRESOLVED"),
                    )
                    session.add(discrepancy_row)

                session.flush()

                stream_elapsed_ms = round((time.perf_counter() - stream_start) * 1000, 2)
                match_rate = round((matched_count / max_population) * 100, 2)
                overall_reports.append({
                    "run_id": run_id,
                    "stream_name": stream,
                    "status": run_status,
                    "mode": mode,
                    "execution_time_ms": stream_elapsed_ms,
                    "started_at": started.isoformat(),
                    "finished_at": finished.isoformat(),
                    "source_count": s_count,
                    "target_count": t_count,
                    "matched_count": matched_count,
                    "mismatched_count": mismatched_count,
                    "match_rate_percent": match_rate,
                    "source_total_bytes": source_summary.get("total_bytes", 0),
                    "target_total_bytes": target_summary.get("total_bytes", 0),
                    "source_table_hash": source_summary.get("table_fingerprint"),
                    "target_table_hash": target_summary.get("table_fingerprint"),
                    "discrepancies_sample": serialize_json_value(discrepancies[:20]),
                    "summary_meta": summary_meta,
                })

        total_elapsed_ms = round((time.perf_counter() - t_start_total) * 1000, 2)
        return {
            "status": next((status for status in ('BLOCKED', 'PARTIAL')
                            if any(row['status'] == status for row in overall_reports)), 'COMPLETED'),
            "executed_at": now().isoformat(),
            "dry_run": dry_run,
            "mode": mode,
            "execution_time_ms": total_elapsed_ms,
            "total_streams": len(overall_reports),
            "reports": overall_reports,
        }
