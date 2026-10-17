# Prompt 03 — Đồng bộ ba tầng, Local/Google Drive và xem tệp

## Kết quả

**PARTIAL toàn giai đoạn; PASS phạm vi code và kiểm thử cô lập.**

- Nhánh: `fix/etl/sync-local-drive-reconciliation`, từ main sạch tại `4424abe` (Prompt 02 đã tích hợp).
- Repository thực tế: `D:/StudyMaterials/HK7/Final_Project/ETL`.
- Không commit, stage, push, merge, sửa .env, credential, model hoặc database Dev/Production.
- Hai stash ban đầu giữ nguyên: `21fe6ab94afb13221855a51d871540a2d46a156e`, `ad07c44d5b6ee5d8efb51c666b133d7f9cddec4d`.
- 8 tài liệu trong TAI_LIEU_DOI_CHIEU có đủ; giữ quyết định pending B03/B04/B07/B08/B09. Không giải quyết lineage/LOV trong giai đoạn này.
- Sequence và kế hoạch tối thiểu trước thay đổi: [sequence.md](sequence.md).
- Chưa nghiệm thu Drive OAuth thật, OCR Tesseract/model thật hoặc browser E2E: NOT_RUN, không coi mocked HTTP/OCR/model là bằng chứng môi trường thật.

## Bằng chứng và nguyên nhân

Đường dẫn code Backend tương đối với `src/data_sync_etl/`; dòng theo worktree hoàn tất.

| Vị trí | Kết quả kiểm tra / sửa |
| --- | --- |
| application/sync.py:24, :66 | Giữ 4 stream, Full/Incremental, checkpoint/replay và transaction theo batch; Mongo RPC sau commit PG. Regression kiểm tra hash/idempotency/null/ownership. |
| adapters/source/postgresql.py:132, :166, :266 | SOURCE adapter dùng REPEATABLE READ + readonly, kiểm role; discovery đọc resource_versions riêng. Không đổi query, danh mục, ID, quan hệ hay schema. |
| adapters/source/aggregate.py:6 | Giữ phân biệt absent/null mongo_id và gộp provider/resource_type/role/education. |
| composition.py:243 | Shared mode mặc định validate metadata, không tự tạo identity/document. Giữ controlled opt-in có guard. |
| application/source_mongo_metadata_sync.py:29; master_metadata_projection.py:46 | Giữ mapping non-PK, extension và quyền sở hữu; shared validation không phải sao chép PG metadata đè Mongo. Regression xác nhận không false drift/mất extension. |
| workers/master_sync.py:13; workers/source_file_import.py:11 | Giữ worker, claim/lease, heartbeat và coordinator cũ; không thêm scheduler/worker. |
| adapters/storage/source_local.py:16, :28 | Giữ absolute/separate roots; traversal, ADS/device, symlink/junction, descriptor guards và read-only. Không fallback provider. |
| adapters/storage/source_dispatcher.py:13; storage/local.py:63 | Giữ định tuyến bucket rõ ràng, staging + hash/format trước atomic publication, không ghi đè file hash đã có. |
| application/source_file_import.py:164, :168, :210, :326 | **Lỗi:** target_version_id tồn tại khiến retry bỏ qua _import và policy nguồn. **Sửa:** kiểm lại Master + source policy trước process/retry, kiểm lại sau copy trước đăng ký version; retry_eligible không cho phép nguồn bị chặn. |
| adapters/storage/source_google_drive.py:203 | **Lỗi:** media HTTP 200 HTML chưa bị chặn tường minh. **Sửa:** từ chối HTML/XHTML/Content-Type không hợp lệ trước staging, giữ đóng response/session và lỗi sanitized. |
| application/search.py:191 | Viewer vẫn yêu cầu verified READY snapshot + whole-file policy; thêm FILE_NOT_READY/409 chỉ cho local caller đủ điều kiện, FILE_NOT_FOUND/404 với thiếu/quyền bị chặn. Diagnostic scan bounded; không cấp quyền trả byte/text ở nhánh 409. |
| api/routes.py:280; api/search_schemas.py:145 | Route file giữ URL/query/download/range/private/nosniff; CSV trả text/csv. Document 409 và lỗi file thay lỗi Context chung. |
| frontend/src/pages/library/ResourceFilePanel.tsx:7, :61 | Báo rõ chưa nhập tệp nguồn / tệp chưa sẵn sàng. Không đổi layout, CSS, submenu/router. |
| frontend/src/pages/library/LibraryResourceDetailPage.tsx:129 | Truyền nguồn sở hữu vào panel cũ, không thêm UI ngoài phạm vi. |
| README.md:300 | Giải thích path Windows, mount container, service name Compose và state/viewer. Không tự thêm service hoặc sửa compose. |

## Hợp đồng bảo toàn

| Thành phần | Trạng thái |
| --- | --- |
| SOURCE | 15 bảng, tên/cột/enum/PK/FK/unique/trigger không đổi. Không thực thi SQL audit trên Dev. |
| Master | 10 bảng nghiệp vụ + 8 runtime; ORM/schema/migration/trigger không đổi. B03/B04/B07/B08/B09 vẫn chờ quyết định. |
| MongoDB | users, learning_resources, ocr_pages, mongo_content_units, content_bags; contract/validator/6 index/dimension 384 không đổi. |
| DDL/migration | **Không có thay đổi hoặc apply trên live.** Chỉ khởi tạo schema bằng installer/migration hiện có trong DB test mới. |
| Migration 0005 | Giữ lịch sử; B13 downgrade index-name mismatch vẫn **KNOWN DEFECT**, không tuyên bố đã sửa. |
| Nội dung/identity | Không đổi ID, source_hash, parent lineage, quyền học liệu hoặc dữ liệu cũ. Không reindex/backfill Dev. |
| Retry | Không đổi budget/count/backoff/stage retry; chỉ chặn bypass policy trên nguồn không còn được phép. |

### Tương thích API

Không thêm API. Route file hiện tại bổ sung 409 để phân biệt chưa sẵn sàng và đổi lỗi Context chung thành lỗi file; Frontend cùng nhánh đã xử lý 409. Client ngoài repository cần xử lý 409 thay vì giả định mọi not-ready đều 404. Trường hợp không có quyền vẫn 404, không trả nội dung hạn chế.

File đã nhập **chưa đủ để preview**: giữ nguyên yêu cầu READY và policy đang có, không bỏ guard để che lỗi storage. Diagnostic 409 không kiểm/chứng minh file đích tồn tại và không trả coordinates.

## Kiểm thử thực tế

Tất cả run dùng PYTHONPATH=src, tắt đọc .env. Unit có audit guard chặn mạng ngoài Windows socketpair nội bộ của TestClient. Integration dùng container mới postgres:16/mongo:8.0, cổng loopback ngẫu nhiên, tmpfs, không mount volume Dev; TEST_* chỉ trỏ vào container mới. Fixture tự tạo schema/database UUID. Đã dọn đúng các container do runner tạo sau từng run.

| Bộ kiểm tra | Kết quả |
| --- | --- |
| Unit regression 7 file dưới đây | **194 PASS**, 0 skip, 91.51s |
| Integration hiện có 7 target dưới đây | **80 PASS**, 0 skip, 78.11s |
| Integration mới test_sync_local_drive_reconciliation.py | **21 PASS**, 0 skip, 51.04s |
| Ruff trên 9 file Backend/test thay đổi | **PASS** |
| Migration governance/static parity | **PASS**, drift=[], SOURCE15/Master18/Mongo5/index6/dimension384; live_applied=false |
| Frontend build | **PASS** |
| Frontend lint | **0 error, 5 warning** set-state-in-effect tại các dòng không thay đổi |
| git diff --check | **PASS** |

Tổng regression chính: **295 PASS** (194 unit + 101 integration). Lần chạy riêng 3 test retry trước khi mở rộng lên 21 cũng PASS, không cộng hai lần.

Unit targets:

```text
tests/unit/test_sync_local_drive_regression.py
tests/unit/test_source_file_import.py
tests/unit/test_source_google_drive.py
tests/unit/test_resource_file.py
tests/unit/test_search.py
tests/unit/test_upload_storage.py
tests/unit/test_domain_storage_source.py
```

Integration targets (80):

```text
tests/integration/source_postgres/test_file_import_flow.py
tests/integration/source_postgres/test_drive_file_flow.py
tests/integration/source_postgres/test_dual_source_metadata_etl.py
tests/integration/source_postgres/test_master_metadata_projection.py
tests/integration/source_postgres/test_resource_ownership.py
tests/integration/source_postgres/test_source.py::test_json_postgresql_equivalence_and_idempotency
tests/integration/test_mongo_schema.py
```

Integration mới:

```text
tests/integration/source_postgres/test_sync_local_drive_reconciliation.py
```

Lệnh lõi của runner: `python -X utf8 -m pytest -p no:cacheprovider -q --basetemp <TEMP_TEST> <targets>`. Runner thực tế gọi pytest.main với cùng tham số sau khi tạo container cô lập, cấu hình TEST_* và đặt Settings.model_config['env_file']=None; không dùng lệnh này với .env/TEST_* trỏ Dev. Không in connection string/password.

Lệnh static/frontend:

```powershell
.\.venv\Scripts\python.exe scripts/check_migration_governance.py
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run lint
git diff --check
```

### Scenarios và bằng chứng

- 9 định dạng PDF/DOCX/XLSX/TXT/CSV/PPTX/PNG/JPG/JPEG × Local/Drive: bytes thật hợp lệ, SHA-256 source=destination; OpenXML đi qua validator/extractor thật.
- Ma trận mới chạy PG/Mongo thật: sau import có target và PROCESSING nhưng chưa ContentUnit/INDEX_BUILD; sau worker đạt CONTENT_READY, ContentUnits được lưu, content_bags vẫn rỗng, đúng 5 collection contract384. Replay không tạo duplicate version/job/import.
- Viewer 18 ca qua FastAPI TestClient: preview/download trả đúng bytes, MIME, disposition, nosniff; Range trả206; source reader bị vô hiệu hóa bằng assertion để chứng minh không proxy nguồn.
- Retry sau copy khi SOURCE REJECTED, SOURCE inactive hoặc Master inactive không gọi process lần hai, không tạo OCR job; bảo toàn version/bytes. Kiểm cả SQLite và PG thật.
- Thu hồi policy trong lúc copy: không đăng ký version/job; sau metadata được cho phép lại có thể retry theo cơ chế cũ.
- Drive ID/folder/name/MIME/shortcut/native-file, source hash mismatch, 401/403/404/429/5xx/timeout, thay đổi version trong download và hủy stream: regression cũ PASS.
- OAuth refresh mock dùng config fixture không chứa secret thật: scope chỉ drive.readonly, max_refresh_attempts=1; RefreshError bị phân loại permanent, requests.Timeout tối đa3 request, không lộ provider detail.
- Local traversal/root separation/missing/permission/symlink; bounded copy/dedup/crash/retry, expired lease và concurrent claim: regression PASS.
- Truy cập viewer bị từ chối, REJECTED, inactive, draft/public/forwarded và không có file: regression fail-closed PASS; readiness không vượt quyền.

## File thay đổi

Backend:
- src/data_sync_etl/application/source_file_import.py
- src/data_sync_etl/adapters/storage/source_google_drive.py
- src/data_sync_etl/application/search.py
- src/data_sync_etl/api/routes.py
- src/data_sync_etl/api/search_schemas.py

Frontend:
- frontend/src/pages/library/LibraryResourceDetailPage.tsx
- frontend/src/pages/library/ResourceFilePanel.tsx

Tests:
- tests/unit/test_resource_file.py
- tests/unit/test_source_google_drive.py
- tests/unit/test_sync_local_drive_regression.py (mới)
- tests/integration/source_postgres/test_sync_local_drive_reconciliation.py (mới)

Docs:
- README.md
- docs/audits/sync-local-drive/sequence.md (mới)
- docs/audits/sync-local-drive/report.md (mới)

## NOT_RUN, rủi ro và đầu vào giai đoạn sau

1. **NOT_RUN live Drive OAuth/folder/download**: không mở credential thật, không gọi Google; không thay scope hoặc tự cấp token. Cần operator chọn file/folder test và chạy read-only check theo README trước nghiệm thu thật.
2. **NOT_RUN Tesseract/model384 thật**: ảnh dùng ImageEngineDouble; indexing regression cũ dùng deterministic model3D trong database test riêng. Contract schema384 được kiểm riêng, không dùng test double làm bằng chứng model thực.
3. **NOT_RUN browser E2E**: build/lint + backend TestClient PASS; chưa xác nhận preview bằng trình duyệt người dùng. Office vẫn dùng viewer/text/download hiện có, không thêm Office renderer.
4. Chưa chạy worker trong Linux app-container với source/token bind mount; Docker chỉ dùng PG/Mongo test. Windows path guards đã test; README mô tả mount cần thiết, không tuyên bố deployment container PASS.
5. Policy nguồn dùng snapshot discovery gần nhất, không bảo đảm quan sát tức thì một thay đổi SOURCE chưa reconcile. Master active kiểm lại mỗi execute; không thay Full/Incremental/Auto để tạo realtime mới.
6. Không mở public access hay thay quyền toàn văn; viewer giữ policy local_demo hiện có. Governance schema còn PARTIAL theo các mục pending của Prompt02.
7. Copy đã publish nhưng policy bị thu hồi trước đăng ký có thể để lại object theo hash chưa có mapping. Không xóa tự động vì có thể được dùng chung; retry sau khi được phép có thể tái dùng đúng bytes.
8. Khi triển khai, client của route file cần nhận biết409. Giai đoạn05 chỉ cần nghiệm thu UI và trạng thái trên dữ liệu test được duyệt, không thiết kế lại layout.

## Rollback / bàn giao

Không có migration/schema/data live cần rollback. Có thể đảo riêng thay đổi code/docs của giai đoạn03 qua review Git; không reset worktree, không xóa stash, không xóa file kho đích hoặc dữ liệu để rollback. Container/fixture test đã dọn; không tác động volume đang sử dụng.

Suggested commit (chưa tạo):
`fix(etl): reconcile source master mongo and stabilize file ingestion`

Hoặc tách tối thiểu: `fix(etl): enforce source policy on import retries and validate Drive media` và `fix(frontend): clarify destination file readiness`, kèm test/docs tương ứng.

