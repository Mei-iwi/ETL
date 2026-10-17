# Prompt 03 — Sequence trước thay đổi

Baseline main 4424abe; Prompt 02 đã merge. Nhánh fix/etl/sync-local-drive-reconciliation.
Giữ nguyên schema SOURCE/Master/Mongo; giữ B04 lineage và B09 LOV.

## Sequence hiện tại

```text
File SOURCE (Local hoặc COURSE_DRIVE, chỉ đọc)
    ↓ tham chiếu bucket/key + SHA-256
SOURCE PG resource_versions + metadata/danh mục
    ↓ MasterSync 4 streams, Full/Incremental + checkpoint
Master PG metadata gộp / SYNC_METADATA
    ↓ discovery đọc source version_page
source_file_imports: PENDING → FETCHING
    ↓ SourceFileDispatcher (không fallback giữa provider)
SOURCE_FILE_AVAILABLE: đọc bytes, folder/path allowlist, bounded stream
    ↓ kiểm size/format/hash; publish atomically bằng content hash
STORAGE_ROOT/resources/... (destination, không phải file nguồn)
    ↓ tạo Master ResourceVersion + target_version_id
IMPORTED (mapping/file đã có; chưa đồng nghĩa đã OCR)
    ↓ process.run tạo job; queue PROCESSING
OCR worker → postprocess → Mongo content projection → CONTENT_READY
    ↓ Index worker/model/schema/fingerprint
INDEX_READY
    ↓ viewer kiểm policy hiện tại rồi đọc file đích (không proxy Drive)
FileResponse: Content-Type, inline/attachment, range
```

IMPORTED là mốc logic target_version_id đã lưu; không tuyên bố có status này được ghi bền trong mọi lần chạy. execute hiện chuyển FETCHING sang PROCESSING sau khi nhận job. Việc file nguồn tồn tại/metadata sync thành công không chứng minh có preview/chunk/bag.

## Bằng chứng và thay đổi dự kiến

| Vị trí | Hiện trạng | Sửa tối thiểu / nguy cơ |
| --- | --- | --- |
| application/source_file_import.py:163/:204/:317 | Policy kiểm trong _import, nhưng retry có target_version_id bỏ qua _import; _eligible chưa kiểm source policy | Kiểm lại trạng thái nguồn/Master trước tạo/retry job, sau copy trước đăng ký version; không thay retry budget hoặc lineage |
| adapters/storage/source_google_drive.py:189 | Metadata MIME có kiểm, media response chưa từ chối HTML rõ ràng | Chặn HTML/XHTML trước staging/format/hash; giữ scope readonly và alt=media, không export |
| application/search.py:191; api/routes.py:279 | Viewer dùng verified index snapshot + whole-file policy; not-ready trả lỗi context chung | Giữ quyền và READY guard; chỉ trả lỗi file/actionable cho local eligible caller, không lộ file/metadata hạn chế |
| frontend/.../ResourceFilePanel.tsx | Không có target version hiện “Chưa có tệp”; 409 không được diễn giải riêng | Thông báo ngắn khi học liệu nguồn chưa imported / file chưa READY; giữ layout/router |

Đường dẫn code tương đối src/data_sync_etl/ trừ frontend.
Không đọc .env/credential, không gọi Drive thật hoặc DB Dev. Kiểm thử bằng fixture files và Drive HTTP double, DB/container test cô lập. Đối chiếu baseline contract bằng checker, không sửa snapshot để che drift.
