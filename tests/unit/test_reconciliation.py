import hashlib
from pathlib import Path

import pytest
from tests.local_admin_client import TestClient

from data_sync_etl.application.reconciliation_engine import (
    FLAG_DESYNC_BLOB_SIZE,
    FLAG_DESYNC_CHECKSUM,
    FLAG_ORPHAN_MASTER,
    FLAG_ORPHAN_SOURCE,
    FLAG_SYNCED,
    ReconciliationEngine,
)
from data_sync_etl.db.models import ResourceVersion
from data_sync_etl.domain.core import new_id, now
from data_sync_etl.main import create_app


def test_compute_table_summary(container):
    """Kiểm tra đối soát vĩ mô và mã băm bảng (Table Fingerprint)."""
    with container.uow() as repo:
        session = repo.session
        summary = container.reconciliation_engine.compute_table_summary(
            session, "subjects", "subjects"
        )
        assert "count" in summary
        assert "min_id" in summary
        assert "max_id" in summary
        assert "table_fingerprint" in summary


def test_verify_file_blobs_fast_and_deep_check(container, tmp_path):
    """Kiểm tra đối soát dung lượng file vật lý 2 cấp:

    - Fast Check: phát hiện mất file hoặc lệch kích thước byte (DESYNC_BLOB_SIZE).
    - Deep Check: phát hiện sai lệch stream SHA-256 (DESYNC_CHECKSUM) và xác nhận đồng bộ (SYNCED).
    """
    storage_root = tmp_path / "test_storage"
    storage_root.mkdir(parents=True, exist_ok=True)
    bucket_dir = storage_root / "learning-resources"
    bucket_dir.mkdir(parents=True, exist_ok=True)

    # 1. Tạo tệp hợp lệ (100% khớp)
    valid_content = b"Hoc lieu so - Mon Toan 12"
    valid_hash = hashlib.sha256(valid_content).hexdigest()
    valid_size = len(valid_content)
    valid_file = bucket_dir / "valid.pdf"
    valid_file.write_bytes(valid_content)

    # 2. Tạo tệp bị lệch kích thước (DESYNC_BLOB_SIZE)
    size_mismatch_content = b"Short content"
    size_file = bucket_dir / "size_mismatch.pdf"
    size_file.write_bytes(size_mismatch_content)

    # 3. Tạo tệp có kích thước bằng nhau nhưng hash khác (DESYNC_CHECKSUM - Silent Corruption)
    corrupted_content = b"Hoc lieu so - Mon Van  12"  # Cùng 25 bytes nhưng nội dung khác
    corrupted_file = bucket_dir / "corrupted.pdf"
    corrupted_file.write_bytes(corrupted_content)

    with container.uow() as repo:
        session = repo.session

        # Cần resource_id hợp lệ
        res_id = new_id()
        repo.insert("master_learning_resources", {
            "id": res_id,
            "source_origin": "LOCAL",
            "resource_type_id": "type-1",
            "resource_type_code": "SGK",
            "resource_type_name_vi": "Sach giao khoa",
            "resource_type_is_active": True,
            "publication_status": "PUBLISHED",
            "is_active": True,
        })

        v1 = ResourceVersion(
            id=new_id(),
            resource_id=res_id,
            version_number=1,
            lifecycle_status="APPROVED",
            storage_bucket="learning-resources",
            storage_object_key="valid.pdf",
            source_hash=valid_hash,
            source_file_size=valid_size,
        )
        v2 = ResourceVersion(
            id=new_id(),
            resource_id=res_id,
            version_number=2,
            lifecycle_status="APPROVED",
            storage_bucket="learning-resources",
            storage_object_key="size_mismatch.pdf",
            source_hash="expected_hash_here",
            source_file_size=valid_size,  # Khai báo 25 bytes nhưng thực tế 13 bytes
        )
        v3 = ResourceVersion(
            id=new_id(),
            resource_id=res_id,
            version_number=3,
            lifecycle_status="APPROVED",
            storage_bucket="learning-resources",
            storage_object_key="corrupted.pdf",
            source_hash=valid_hash,  # Khai báo valid_hash nhưng thực tế corrupted
            source_file_size=len(corrupted_content),
        )
        v4 = ResourceVersion(
            id=new_id(),
            resource_id=res_id,
            version_number=4,
            lifecycle_status="APPROVED",
            storage_bucket="learning-resources",
            storage_object_key="missing.pdf",  # File không tồn tại trên đĩa
            source_hash=valid_hash,
            source_file_size=valid_size,
        )
        session.add_all([v1, v2, v3, v4])
        session.flush()

        engine = ReconciliationEngine(container.uow, storage_root=storage_root)
        results = engine.verify_file_blobs(session, storage_root=storage_root)

        result_by_id = {r["record_id"]: r for r in results}

        assert result_by_id[v1.id]["discrepancy_flag"] == FLAG_SYNCED
        assert result_by_id[v2.id]["discrepancy_flag"] == FLAG_DESYNC_BLOB_SIZE
        assert result_by_id[v3.id]["discrepancy_flag"] == FLAG_DESYNC_CHECKSUM
        assert result_by_id[v4.id]["discrepancy_flag"] == FLAG_DESYNC_BLOB_SIZE


def test_reconciliation_engine_safety_bounds_blocked(container):
    """Kiểm tra ngưỡng an toàn tự phục hồi (max_ratio <= 10%).

    Nếu tỷ lệ lệch lớn hơn 10%, trạng thái chuyển thành BLOCKED và ghi cờ BLOCKED_MASS_DISCREPANCY.
    """
    engine = ReconciliationEngine(container.uow, max_ratio=0.05)  # 5% ngưỡng

    # Tạo 20 bản ghi ở Master mà Source không có (tỷ lệ lệch 100% > 5%)
    with container.uow() as repo:
        for i in range(20):
            repo.sync_upsert("master_learning_resources", {
                "id": f"orphan-res-{i:03d}",
                "resource_type_id": "type-1",
                "resource_type_code": "SGK",
                "resource_type_name_vi": "Sach",
                "resource_type_is_active": True,
                "publication_status": "PUBLISHED",
                "is_active": True,
                "created_at": now(),
                "updated_at": now(),
            }, source_origin="POSTGRESQL")

    result = engine.execute_stream_reconcile(stream_name="resources", dry_run=True)
    report = result["reports"][0]

    assert report["status"] == "BLOCKED"
    assert report["summary_meta"]["guard_alert"] == "BLOCKED_MASS_DISCREPANCY"
    assert report["mismatched_count"] > 0


def test_reconciliation_api_endpoints(container):
    """Kiểm tra 3 REST API endpoint đối soát dữ liệu."""
    container.settings.admin_enabled = True
    app = create_app(container)
    with TestClient(app) as client:
        # 1. Kích hoạt đối soát mới: POST /api/v1/reconciliation/runs
        trigger_res = client.post(
            "/api/v1/reconciliation/runs",
            json={"stream": "subjects", "dry_run": True},
        )
        assert trigger_res.status_code == 200
        data = trigger_res.json()
        assert "status" in data
        assert "reports" in data
        assert len(data["reports"]) == 1

        run_id = data["reports"][0]["run_id"]

        # 2. Lấy dashboard KPI mới nhất: GET /api/v1/reconciliation/runs/latest
        latest_res = client.get("/api/v1/reconciliation/runs/latest?stream=subjects")
        assert latest_res.status_code == 200
        latest_data = latest_res.json()
        assert latest_data["latest_run_id"] == run_id
        assert "overall_match_rate_percent" in latest_data
        assert "stream_reports" in latest_data

        # 3. Lấy danh sách bản ghi lệch: GET /api/v1/reconciliation/discrepancies
        disc_res = client.get(f"/api/v1/reconciliation/discrepancies?run_id={run_id}&page=1&page_size=10")
        assert disc_res.status_code == 200
        disc_data = disc_res.json()
        assert "items" in disc_data
        assert "total" in disc_data
        assert "page" in disc_data
        assert "total_pages" in disc_data
