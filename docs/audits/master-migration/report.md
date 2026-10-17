# Prompt 02 — Governance migration/trigger, giữ nguyên schema

Ngày 17/10/2026. Nhánh: fix/db/master-schema-migration-governance, tạo từ main sạch tại 753fb22 sau merge Prompt 01.

## Kết quả và phạm vi

**Governance và kiểm thử: PASS. Tổng thể: PARTIAL vì B13 lịch sử chưa sửa và các thay đổi thiết kế chờ phê duyệt.**
Theo xác nhận của người dùng, tiếp tục phần độc lập; chỉ BLOCKED riêng các thay đổi bảng/cột/FK/quan hệ, không dừng toàn bộ Prompt 02.

| Tầng | Before → After | Kết quả |
| --- | --- | --- |
| SOURCE | 15 bảng, 145 cột, 22 FK, 7 enum, 26 trigger → giữ nguyên | Reflection/replay/trigger test PASS |
| Master | 10 logical + 8 runtime = 18 bảng, 233 cột; head 0015 → giữ nguyên | Parity ORM/replay/trigger test PASS |
| Mongo | 5 collection, 6 business index, dimension 384 → giữ nguyên | Check/idempotency/preserve test PASS |

Không thêm 0016/s0004, không sửa checksum revision cũ, không thay ORM/adapter/API/Frontend. Không đổi .env, không đọc secrets, không chạy migration/backup trên Dev/Production, không commit/push/merge. Hai stash có sẵn được giữ nguyên.

8 tài liệu gốc và schema/migration implementation đã đối chiếu SHA-256 với snapshot Phase 01; không đổi. Hai ảnh ERD được xem lại; Excel được đọc read-only theo skill Spreadsheets bằng openpyxl do Artifact Tool không có trong môi trường. B01 las_seq/last_seq và B02 mô tả Fallback vẫn là quyết định tài liệu chưa được duyệt, không âm thầm sửa.

## Bằng chứng trước thay đổi

| Vị trí | Hiện trạng / lý do | Thay đổi ít xâm lấn |
| --- | --- | --- |
| alembic/versions/0006_source_compatibility.py:52, 0007_timestamp_integrity.py:16 | Trigger inline lịch sử; không được chuyển hoặc sửa nguồn replay | Thêm canonical reference, đối chiếu AST và PostgreSQL |
| alembic_source/triggers_v1.sql:1, timestamps_v2.sql:1, s0002/s0003 | SOURCE 26 trigger, timestamp head khác bản v1 | Reference head s0003, không chỉnh SQL gốc |
| adapters/mongo/schema.py:15, :261 | SCHEMA_VERSION, 5 validator, 6 index, CLI đã có | Manifest hash/check-only; không có engine apply mới |
| alembic/versions/0005_subject_query_indexes.py:10/:38 | Tạo ix_subject_grade_active_deleted nhưng downgrade bỏ ix_subjects_grade_active_deleted | Tái hiện bằng test XFAIL, phương án operator-controlled; không rewrite |
| db/models.py:57/:101/:134, application/source_file_import.py:253, list_resource_lookups.py:74 | FK creator, width grade, môn/khối, lineage/LOV còn quyết định | Bảng phương án riêng; không DDL |

Các code path adapters/application/db tương đối src/data_sync_etl/. Báo cáo gốc và mapping 233 trường: ../erd-contract/read_only_audit_report.md và master_mapping_matrix.csv.

## File mới / cách dùng

- scripts/check_migration_governance.py: CLI read-only, kiểm hash baseline, hai chain, references, manifest và SQL SELECT-only; không có --apply hoặc URL tùy ý; guard chặn .env/network.
- alembic/sql/triggers/master_current.sql và README.md: canonical 2 function/6 trigger Master hiện tại.
- alembic_source/sql/triggers/source_current.sql: canonical 4 function/26 trigger SOURCE hiện tại.
- scripts/sql/verify/catalog.sql, master_preflight.sql, README.md: catalog và số liệu preflight, không mặc định schema public, không DML/DDL.
- migrations_mongo/manifest.json, README.md: pin schema version/validator/index hashes và 384D; trạng thái external Git, không collection thứ sáu.
- tests/unit/test_migration_governance.py, tests/integration/test_migration_governance.py, tests/integration/source_postgres/test_migration_governance.py: governance/tamper, parity, function bodies, read-only SQL và tái hiện 0005.
- docs/audits/master-migration/decisions.json: ghi đúng phê duyệt governance/preserve; approved_schema_changes rỗng.
- Báo cáo này và design_options.md: phương án và migration matrix.

Chạy từ root checkout:

```powershell
.\.venv\Scripts\python.exe scripts/check_migration_governance.py
.\.venv\Scripts\ruff.exe check --no-cache scripts/check_migration_governance.py tests/unit/test_migration_governance.py tests/integration/test_migration_governance.py tests/integration/source_postgres/test_migration_governance.py
```

Checker PASS chỉ chứng nhận repository, không đánh dấu live schema_ready. Không chạy lại --write của audit Phase 01 để “hợp thức hóa” drift; baseline cũ giữ nguyên, so sánh contract chứ không ép HEAD sau merge bằng HEAD audit.

## Tổ chức SQL / packaging

Reference SQL không được migration/API load, không phải installer. Historical SQL và inline revision giữ nguyên. Checker lấy literal SQL từ AST của upgrade, không import/chạy revision.
PostgreSQL test đối chiếu chính xác tên trigger và body function cài bởi chain gốc, không cài bằng reference để tự chứng minh chính nó.

Docker hiện COPY hai thư mục Alembic nên có references. Không có revision mới gọi SQL asset; wheel runtime loading NOT_APPLICABLE. Nếu cần executable SQL mới sau phê duyệt, phải package asset theo phiên bản, dùng đường dẫn ổn định/importlib.resources và test wheel/Docker; không redirect revision cũ sang file current.

Mongo manifest là external contract baseline, không phải biên nhận migration đã chạy. Bootstrap-empty, check và controlled upgrade phân biệt rõ; không tự gọi --apply trên DB cũ. Upgrade path chưa được phê duyệt nên chỉ có plan + verification, không giả mạo migration hoàn chỉnh.

## Lỗi downgrade 0005 — đã tái hiện, chưa sửa lịch sử

Tại revision 0004 sau downgrade, ix_subject_grade_active_deleted còn tồn tại vì câu DROP dùng tên có thêm chữ s. if_exists=True làm thao tác bỏ qua thay vì báo lỗi. Test mong muốn không còn index được đánh dấu XFAIL strict để không che giấu lỗi; đây không phải PASS của downgrade đó.

Không tạo 0016 chỉ để “sửa” lỗi này: revision mới không thay đổi mã downgrade 0005 khi sau này đi qua nó.
Phương án an toàn để phê duyệt riêng:

1. Giữ nguyên 0005 và snapshot/hash lịch sử. Không chạy downgrade cũ vô giám sát.
2. Operator xác nhận target/backup/restore/maintenance window và revision đích; preflight pg_indexes/pg_depend cho đúng schema, index name, non-unique và ba cột grade_id,is_active,deleted_at.
3. Nếu thật sự cần rollback qua 0005, bổ sung bước cleanup đúng một index trong quy trình operator-approved; đặt lock_timeout/statement_timeout, transaction phù hợp và post-check. Không drop theo glob hoặc sửa dữ liệu.
4. Khôi phục index theo definition đã chụp nếu hủy rollback. Upgrade lại 0005 phải xác minh index đúng tên/cột.
5. Nếu tổ chức chọn sửa revision chưa từng triển khai, phải chứng minh trạng thái đó trước; ở repo này không được mặc định như vậy.

Chưa có command/script cleanup ghi DB và chưa thực thi cleanup. Chỉ verification/reference/test được bổ sung.

## Kiểm thử thực tế

Docker kiểm tra chỉ đọc ban đầu cần quyền truy cập daemon; sau đó dùng image sẵn có postgres:16 và mongo:8.0, không pull/install dependency.
Tạo đúng hai container riêng theo UUID/label etl.phase2-test; port host ngẫu nhiên bind 127.0.0.1, PG/Mongo data là tmpfs, không bind/named/anonymous volume Dev. Mật khẩu test sinh trong process, không in hoặc lưu Git. Tạo etl_test_master/etl_test_source trên cluster mới; các fixture dùng schema/database etl_test_* UUID.
Settings.env_file bị tắt trước import app/migration; guard chặn mở .env. Chỉ TEST_* và cấu hình process trỏ vào hai container mới.

| Bộ kiểm tra | Kết quả |
| --- | --- |
| Checker static; hash 8 reference/schema/revision; 2 chains | PASS, drift=[] |
| Unit/regression 5 file: governance, schema_alignment, resource_metadata_ownership, mongo_foundation, source_ids | 92 PASS, 1 deselected |
| PostgreSQL/Mongo cô lập: Master 18-table parity, SOURCE ERD 145-field replay, trigger names/body hai tầng, preflight READ ONLY, 0013→head/repeated head, sync/ownership/Mongo non-destructive/idempotency/384D | 16 PASS, 1 XFAIL (B13) |
| Ruff check/format-check trên 4 file Python mới | PASS |
| git diff --check + whitespace file mới | PASS |
| Backup/live catalog/apply Dev/Production | NOT_RUN, không có phê duyệt |
| Frontend/E2E/model inference | NOT_RUN, không có thay đổi trong scope |

Bộ unit ban đầu gặp tmp pytest cũ bị khóa và guard cấm socketpair Windows của TestClient. Đã dùng thư mục temp mới và cho phép riêng stdlib _fallback_socketpair loopback trong harness; vẫn chặn kết nối khác/.env. Không sửa code ứng dụng để làm test pass.
Các temp fixture và hai container mới đã được dọn; chỉ dữ liệu test vừa tạo bị loại bỏ theo cleanup của fixture, không thể/không cần khôi phục. Không đụng container/volume/dữ liệu cũ.

## Migration matrix / rollback / điểm chờ

Xem [design_options.md](design_options.md) cho từng phương án có Before/After/Expected ERD/Reason/Rollback/LIVE_APPLIED.
Governance có thể bàn giao độc lập; BLOCKED riêng B03/B04/B07/B08/B09 trước mọi schema/business change. B13 còn known historical defect, B01/B02 cần chốt tài liệu.

Không có schema delta để rollback. Bỏ các file governance mới chỉ sau quyết định người dùng, không cần DB rollback; không tự xóa stash.
Đề xuất commit, chưa tạo: chore(db): add migration governance and isolated schema tests.

## Nghiệm thu và bàn giao Prompt 03

Ngày 17/10/2026, người dùng đã chấp nhận nghiệm thu governance/tests với 92 unit PASS, 16 integration PASS và 1 XFAIL của B13. Trạng thái toàn giai đoạn vẫn PARTIAL; XFAIL là KNOWN DEFECT, NOT_FIXED, không phải lỗi đã sửa.

- SOURCE giữ 15 bảng; MongoDB giữ 5 collection và hợp đồng hiện tại.
- B03/B07/B08: chưa có phê duyệt schema Master, không thêm bảng/cột/FK hoặc thay quan hệ.
- B04 lineage và B09 LOV: giữ nguyên hành vi hiện tại cho tới quyết định của người dùng, kể cả phương án chỉ sửa adapter/usecase không cần DDL.
- SQL current chỉ là tham chiếu; manifest chỉ kiểm tra contract; revision Alembic/CLI installer hiện có mới là đường thực thi migration. Không dùng tài liệu nghiệm thu làm quyền apply.
- Prompt 03 chỉ tiếp tục công việc độc lập với các mục đang giữ nguyên sau khi đọc prompt thực tế; chưa triển khai logic Prompt 03 trong lần bàn giao này.
- Không commit, stage hoặc push. Không thay .env, database Dev, migration 0005 hay dữ liệu lịch sử.

## Danh sách đề xuất commit

Nên tạo một commit gồm đúng 15 file dưới đây để checker, manifest, decision register và test đi cùng nhau. Không dùng git add . vì có thể gom thay đổi ngoài phạm vi khi bàn giao.

| Nhóm | File |
| --- | --- |
| SQL tham chiếu | alembic/sql/triggers/README.md |
| SQL tham chiếu | alembic/sql/triggers/master_current.sql |
| SQL tham chiếu | alembic_source/sql/triggers/source_current.sql |
| Mongo governance | migrations_mongo/README.md |
| Mongo governance | migrations_mongo/manifest.json |
| Checker | scripts/check_migration_governance.py |
| SQL kiểm tra | scripts/sql/verify/README.md |
| SQL kiểm tra | scripts/sql/verify/catalog.sql |
| SQL kiểm tra | scripts/sql/verify/master_preflight.sql |
| Unit test | tests/unit/test_migration_governance.py |
| Integration Master | tests/integration/test_migration_governance.py |
| Integration SOURCE | tests/integration/source_postgres/test_migration_governance.py |
| Nghiệm thu/quyết định | docs/audits/master-migration/decisions.json |
| Phương án chờ duyệt | docs/audits/master-migration/design_options.md |
| Báo cáo/bàn giao | docs/audits/master-migration/report.md |

Không đưa vào commit: .env/credentials, DB dump, model/file học liệu, container data, migration lịch sử, schema runtime, ảnh/Excel tham chiếu hoặc bất kỳ thay đổi lineage/LOV nào.
