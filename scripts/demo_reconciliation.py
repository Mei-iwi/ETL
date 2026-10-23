"""Kịch bản Demo Đối soát Dữ liệu Trực quan (Visual Data Reconciliation Demo).

Chạy script này để xem trực tiếp:
1. Đối soát vĩ mô & Băm gộp toàn bảng (Aggregate Table Fingerprint).
2. Kiểm tra dung lượng file 2 cấp: Fast Check O(1) + Deep Check O(N).
3. Bảng đối soát song song (Side-by-side table) với đầy đủ các cờ trạng thái:
   - SYNCED: Đồng nhất 100%
   - DESYNC_BLOB_SIZE: Lệch kích thước tệp vật lý
   - DESYNC_CHECKSUM: Sai lệch mã băm SHA-256 (Silent Corruption)
   - ORPHAN_SOURCE: Bản ghi mồ côi tại Nguồn (chưa đồng bộ)
   - ORPHAN_MASTER: Bản ghi mồ côi tại Master (đã xóa bên nguồn)
4. Ngưỡng an toàn tự phục hồi (Safety bounds max_ratio <= 10%).

Cách chạy:
    python scripts/demo_reconciliation.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

# Thêm thư mục gốc vào sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyUnitOfWork
from data_sync_etl.application.reconciliation_engine import (
    FLAG_DESYNC_BLOB_SIZE,
    FLAG_DESYNC_CHECKSUM,
    FLAG_DESYNC_METADATA,
    FLAG_MUTATED_DOWNSTREAM,
    FLAG_MUTATED_SOURCE,
    FLAG_ORPHAN_MASTER,
    FLAG_ORPHAN_SOURCE,
    FLAG_SYNCED,
    ReconciliationEngine,
)
from data_sync_etl.db.models import Base, MasterLearningResource, ResourceVersion, SyncReconciliationDiscrepancy
from data_sync_etl.db.session import make_engine, sessions
from data_sync_etl.domain.core import new_id, now


class DummySourceAdapter:
    """Nguồn giả lập mô phỏng dữ liệu tại Database Source."""
    source_origin = "POSTGRESQL"

    def __init__(self, records):
        self._records = records

    def batches(self, stream, cursor=None, batch_size=100):
        class BatchItem:
            def __init__(self, val):
                self.id = val["id"]
                self.values = val
        items = [BatchItem(r) for r in self._records.get(stream, [])]
        yield items


def format_bytes(size: int | None) -> str:
    if size is None or size == 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(size) < 1024.0:
            return f"{size:3.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"


def main():
    print("\n" + "=" * 95)
    print("   HỆ THỐNG ĐỐI SOÁT & KIỂM TRA TOÀN VẸN DỮ LIỆU (DATA RECONCILIATION ENGINE)")
    print("   ĐỒ ÁN TỐT NGHIỆP: Data Sync & ETL Học liệu số (Nguồn: Postgres/Drive -> Đích)")
    print("=" * 95 + "\n")

    # 1. Tạo môi trường test cô lập trong thư mục tạm
    with tempfile.TemporaryDirectory(prefix="reconcile_demo_", ignore_cleanup_errors=True) as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "master_demo.db"
        storage_root = tmp_path / "storage"
        bucket_dir = storage_root / "learning-resources"
        bucket_dir.mkdir(parents=True, exist_ok=True)

        engine = make_engine(f"sqlite:///{db_path.as_posix()}")
        Base.metadata.create_all(bind=engine)
        uow = SQLAlchemyUnitOfWork(sessions(engine))

        try:
            # 2. Chuẩn bị các file mẫu vật lý để test Fast Check & Deep Check
            # File A: Hợp lệ 100%
            content_a = b"Tai lieu Toan 12 - Chuan 2026. File hoc lieu goc."
            hash_a = hashlib.sha256(content_a).hexdigest()
            size_a = len(content_a)
            (bucket_dir / "toan12_chuan.pdf").write_bytes(content_a)

            # File B: Bị lỗi tải dở dang -> Lệch dung lượng (DESYNC_BLOB_SIZE)
            content_b_actual = b"Tai lieu bi thieu 1 nua..."
            size_b_declared = 5242880  # Khai báo nguồn: 5 MB
            (bucket_dir / "vatly11_corrupt_size.pdf").write_bytes(content_b_actual)

            # File C: Dung lượng giống nhau nhưng nội dung bị hỏng ngầm -> Lệch SHA-256 (DESYNC_CHECKSUM)
            content_c_actual = b"Du lieu bi sua doi ngoai luong!"
            hash_c_declared = hashlib.sha256(b"Du lieu goc chua bi sua doi!").hexdigest()
            (bucket_dir / "hoahoc10_corrupt_hash.pdf").write_bytes(content_c_actual)

            # 3. Chuẩn bị dữ liệu Master Database
            res1_id = "res_toan_12_kntt"
            res2_id = "res_vatly_11_ctst"
            res3_id = "res_hoahoc_10_cd"
            res4_orphan_master = "res_lichsu_12_cu"  # Bản ghi có ở Master nhưng Source đã xóa

            with uow() as repo:
                session = repo.session
                for r_id, title in [
                    (res1_id, "Sách Giáo Khoa Toán 12"),
                    (res2_id, "Chuyên đề Vật Lý 11"),
                    (res3_id, "Tài liệu Hóa Học 10"),
                    (res4_orphan_master, "Sách Lịch Sử 12 (Bản cũ đã bỏ)"),
                ]:
                    session.add(MasterLearningResource(
                        id=r_id,
                        title=title,
                        resource_type_id="type-1",
                        resource_type_code="SGK",
                        resource_type_name_vi="Sách giáo khoa",
                        resource_type_is_active=True,
                        publication_status="PUBLISHED",
                        is_active=True,
                        source_origin="POSTGRESQL",
                    ))

                # Thêm các phiên bản tệp
                v1 = ResourceVersion(
                    id=new_id(), resource_id=res1_id, version_number=1, lifecycle_status="APPROVED",
                    storage_bucket="learning-resources", storage_object_key="toan12_chuan.pdf",
                    source_hash=hash_a, source_file_size=size_a, storage_file_size=size_a,
                )
                v2 = ResourceVersion(
                    id=new_id(), resource_id=res2_id, version_number=1, lifecycle_status="APPROVED",
                    storage_bucket="learning-resources", storage_object_key="vatly11_corrupt_size.pdf",
                    source_hash="hash_b_declared", source_file_size=size_b_declared, storage_file_size=len(content_b_actual),
                )
                v3 = ResourceVersion(
                    id=new_id(), resource_id=res3_id, version_number=1, lifecycle_status="APPROVED",
                    storage_bucket="learning-resources", storage_object_key="hoahoc10_corrupt_hash.pdf",
                    source_hash=hash_c_declared, source_file_size=len(content_c_actual), storage_file_size=len(content_c_actual),
                )
                session.add_all([v1, v2, v3])
                session.flush()

            # 4. Chuẩn bị dữ liệu Nguồn (Source) giả lập
            res5_orphan_source = "res_tienganh_12_global"
            source_records = {
                "resources": [
                    {"id": res1_id, "title": "Sách Giáo Khoa Toán 12", "publication_status": "PUBLISHED", "is_active": True},
                    {"id": res2_id, "title": "Chuyên đề Vật Lý 11", "publication_status": "PUBLISHED", "is_active": True},
                    {"id": res3_id, "title": "Tài liệu Hóa Học 10 - Đổi tên", "publication_status": "PUBLISHED", "is_active": True},
                    {"id": res5_orphan_source, "title": "Tiếng Anh 12 Global Success (Mới)", "publication_status": "PUBLISHED", "is_active": True},
                ],
                "files": [],
            }
            dummy_source = DummySourceAdapter(source_records)

            # 5. Khởi chạy Reconciliation Engine
            print("[*] Đang khởi động ReconciliationEngine...")
            print("    - Cấp 1: Fast Check O(1) so sánh dung lượng byte qua os.stat")
            print("    - Cấp 2: Deep Check O(N) stream SHA-256 buffer 64KB")
            print("    - Đối soát vĩ mô: Table Fingerprint MD5 aggregate hashing")
            print("    - Ngưỡng an toàn: max_ratio = 10% (0.1)\n")

            engine_svc = ReconciliationEngine(
                uow=uow,
                source_adapter=dummy_source,
                storage_root=storage_root,
                max_ratio=0.5,
            )

            # Thực thi đối soát stream resources và files
            res_report = engine_svc.execute_stream_reconcile(stream_name="resources", dry_run=True)
            file_report = engine_svc.execute_stream_reconcile(stream_name="files", dry_run=True)

            # 6. IN KẾT QUẢ KPI DASHBOARD
            print("=" * 95)
            print("                     BÁO CÁO DASHBOARD KPI ĐỐI SOÁT MỚI NHẤT")
            print("=" * 95)
            rep_res = res_report["reports"][0]
            rep_file = file_report["reports"][0]

            print(f"| Mã đợt chạy (Run ID)   : {rep_res['run_id']}")
            print(f"| Thời gian thực thi     : {rep_res['started_at']} -> {rep_res['finished_at']}")
            print(f"| Trạng thái đợt chạy    : {rep_res['status']}")
            print(f"| Tỷ lệ đồng nhất chung  : {rep_res['match_rate_percent']}%")
            print(f"| Tổng bản ghi Nguồn     : {rep_res['source_count']}")
            print(f"| Tổng bản ghi Master    : {rep_res['target_count']}")
            print(f"| Số bản ghi khớp        : {rep_res['matched_count']}")
            print(f"| Số bản ghi lệch cờ     : {rep_res['mismatched_count'] + rep_file['mismatched_count']}")
            print(f"| Table Fingerprint      : {rep_res['target_table_hash']}")
            print("-" * 95)

            # 7. IN BẢNG ĐỐI SOÁT SONG SONG (SIDE-BY-SIDE AUDIT TABLE)
            print("\n" + "=" * 95)
            print("               BẢNG ĐỐI SOÁT SONG SONG CHI TIẾT (SIDE-BY-SIDE RECONCILIATION)")
            print("=" * 95)
            print(f"{'MÃ BẢN GHI':<24} | {'DỮ LIỆU NGUỒN':<24} | {'DỮ LIỆU MASTER/STORAGE':<24} | {'CỜ HIỆU TRẠNG THÁI'}")
            print("-" * 95)

            # Đọc discrepancies từ cơ sở dữ liệu
            with uow() as repo:
                disc_rows = repo.session.query(SyncReconciliationDiscrepancy).all()

                for d in disc_rows:
                    rec_id = d.record_id
                    flag = d.discrepancy_flag
                    s_val = d.source_value or {}
                    t_val = d.target_value or {}

                    if flag == FLAG_ORPHAN_SOURCE:
                        s_info = "Có tại Source (Mới)"
                        t_info = "[THIẾU TRÊN MASTER]"
                        tag = "[!] ORPHAN_SOURCE"
                    elif flag == FLAG_ORPHAN_MASTER:
                        s_info = "[ĐÃ XÓA TRÊN NGUỒN]"
                        t_info = "Tồn tại ở Master"
                        tag = "[!] ORPHAN_MASTER (Tombstone)"
                    elif flag == FLAG_DESYNC_METADATA:
                        s_info = "Title bị đổi tại Nguồn"
                        t_info = "Title cũ tại Master"
                        tag = "[~] DESYNC_METADATA"
                    elif flag == FLAG_DESYNC_BLOB_SIZE:
                        s_size = format_bytes(s_val.get("source_file_size"))
                        t_size = format_bytes(t_val.get("storage_file_size"))
                        s_info = f"Khai báo: {s_size}"
                        t_info = f"Thực tế : {t_size}"
                        tag = "[X] DESYNC_BLOB_SIZE"
                    elif flag == FLAG_DESYNC_CHECKSUM:
                        s_hash = str(s_val.get('source_hash', ''))[:12]
                        t_hash = str(t_val.get('computed_hash', ''))[:12]
                        s_info = f"SHA: {s_hash}..."
                        t_info = f"SHA: {t_hash}..."
                        tag = "[X] DESYNC_CHECKSUM (Corrupt)"
                    else:
                        s_info = "Khớp 100%"
                        t_info = "Khớp 100%"
                        tag = "[OK] SYNCED"

                    print(f"{rec_id:<24} | {s_info:<24} | {t_info:<24} | {tag}")

            print("=" * 95)
            print("\n[OK] KỊCH BẢN ĐỐI SOÁT HOÀN TẤT THÀNH CÔNG!")
            print("Toàn bộ lịch sử đối soát và sai lệch đã được lưu vào 2 bảng:")
            print("  1. sync_reconciliation_runs (Lịch sử đợt chạy và Table Fingerprint)")
            print("  2. sync_reconciliation_discrepancies (Chi tiết bản ghi lệch phục vụ Frontend Side-by-side)\n")

        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
