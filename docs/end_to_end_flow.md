# Master Sync và Resource ETL là hai workflow độc lập

```mermaid
sequenceDiagram
    participant Admin as CLI/API
    participant Sync as MasterSync
    participant Source as SourceDataReader
    participant DB as PostgreSQL
    Admin->>Sync: sync-master full/incremental
    Sync->>Source: batches(cursor)
    loop mỗi batch
        Sync->>DB: lock checkpoint + upsert aggregate/relations
        Sync->>DB: commit checkpoint cùng batch
    end
    Sync-->>Admin: counters + run_id
```

```mermaid
sequenceDiagram
    participant Admin as CLI/API
    participant ETL as EtlOrchestrator
    participant DB as PostgreSQL
    participant Worker as Process OCR
    Admin->>ETL: start(resource, file), sync_master=false
    ETL->>DB: validate resource đã có, active
    ETL->>DB: hash snapshot + lock + ingest version
    alt cùng hash latest
        ETL-->>Admin: NO_CHANGE
    else version mới
        ETL->>DB: create job + page tasks
        Worker->>DB: claim SKIP LOCKED + lease
        Worker->>Worker: native text hoặc OCR engine
        Worker->>DB: result + task + atomic counters
        Admin->>ETL: postprocess/resume khi job terminal
        ETL->>DB: atomic units + stage audit
        ETL-->>Admin: count + coverage
    end
```

`start(..., sync_master=False)` không gọi sync. Convenience `sync_master=True` vẫn được giữ nhưng chỉ
incremental all streams, không tự full sync hay giả lập sync một resource. Ingestion guard từ chối master
thiếu/inactive/deleted. API ingest vốn gọi ResourceIngestion trực tiếp, không thay đổi contract.

Demo CLI explicit full sync khi fixture resource chưa có; lần sau không seed lại.
CLI là công cụ orchestration local; API không chạy worker loop. Sau crash giữa ingest/job creation,
gọi create-ocr-job hoặc resume-etl bằng version ID. Sau crash worker: recover-stale rồi chạy worker.
Postprocess rerun idempotent, rollback giữ units cũ. Audit RUNNING cũ có thể còn nếu process chết.

`etl-status` trả resource/version/latest version, job/counters, tối đa 20 stage gần nhất và content count;
không trả users/password/OCR text. Correlation ID theo invocation, nối process qua version/job/task IDs.
