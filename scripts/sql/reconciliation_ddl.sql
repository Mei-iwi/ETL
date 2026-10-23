-- ==============================================================================
-- DDL MIGRATION: DATA RECONCILIATION & INTEGRITY VERIFICATION SYSTEM
-- Áp dụng cho: PostgreSQL Master Database (etl)
-- ==============================================================================

-- 1. Bổ sung trường kích thước byte vào bảng resource_versions và source_file_imports
ALTER TABLE resource_versions
    ADD COLUMN IF NOT EXISTS source_file_size BIGINT NULL,
    ADD COLUMN IF NOT EXISTS storage_file_size BIGINT NULL;

COMMENT ON COLUMN resource_versions.source_file_size IS 'Kích thước file gốc khai báo từ Source/Drive (Bytes)';
COMMENT ON COLUMN resource_versions.storage_file_size IS 'Kích thước file thực tế lưu trong Storage disk Master (Bytes)';

ALTER TABLE source_file_imports
    ADD COLUMN IF NOT EXISTS source_file_size BIGINT NULL,
    ADD COLUMN IF NOT EXISTS storage_file_size BIGINT NULL;

COMMENT ON COLUMN source_file_imports.source_file_size IS 'Kích thước file gốc tải về từ Source (Bytes)';
COMMENT ON COLUMN source_file_imports.storage_file_size IS 'Kích thước file thực tế lưu tại Storage Master (Bytes)';


-- 2. Bảng quản lý lịch sử từng đợt đối soát
CREATE TABLE IF NOT EXISTS sync_reconciliation_runs (
    run_id VARCHAR(36) PRIMARY KEY,
    stream_name VARCHAR(50) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'RUNNING',
    source_count INTEGER NOT NULL DEFAULT 0,
    target_count INTEGER NOT NULL DEFAULT 0,
    matched_count INTEGER NOT NULL DEFAULT 0,
    mismatched_count INTEGER NOT NULL DEFAULT 0,
    source_total_bytes BIGINT DEFAULT 0,
    target_total_bytes BIGINT DEFAULT 0,
    source_table_hash VARCHAR(64) NULL,
    target_table_hash VARCHAR(64) NULL,
    summary_meta JSONB NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Index phục vụ lọc báo cáo theo luồng và sắp xếp thời gian chạy mới nhất
CREATE INDEX IF NOT EXISTS ix_reconcile_runs_stream_started
    ON sync_reconciliation_runs (stream_name, started_at DESC);


-- 3. Bảng lưu chi tiết các bản ghi bị lệch trong đợt đối soát
CREATE TABLE IF NOT EXISTS sync_reconciliation_discrepancies (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL REFERENCES sync_reconciliation_runs(run_id) ON DELETE CASCADE,
    record_id VARCHAR(100) NOT NULL,
    stream_name VARCHAR(50) NOT NULL,
    discrepancy_flag VARCHAR(50) NOT NULL,
    source_value JSONB NULL,
    target_value JSONB NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolution_status VARCHAR(30) NOT NULL DEFAULT 'UNRESOLVED'
);

-- Các Index tối ưu cho truy vấn lọc báo cáo
CREATE INDEX IF NOT EXISTS ix_reconcile_discrepancies_run_flag
    ON sync_reconciliation_discrepancies (run_id, discrepancy_flag);

CREATE INDEX IF NOT EXISTS ix_reconcile_discrepancies_stream_status
    ON sync_reconciliation_discrepancies (stream_name, resolution_status);

CREATE INDEX IF NOT EXISTS ix_reconcile_discrepancies_record
    ON sync_reconciliation_discrepancies (record_id);

COMMENT ON TABLE sync_reconciliation_runs IS 'Lịch sử và thống kê KPI các đợt đối soát dữ liệu Nguồn - Đích';
COMMENT ON TABLE sync_reconciliation_discrepancies IS 'Chi tiết các bản ghi sai lệch phát hiện trong đối soát (Side-by-side discrepancy log)';
