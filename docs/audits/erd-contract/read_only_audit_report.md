# Prompt 01 — Kiểm toán hợp đồng ERD

Ngày: 17/10/2026. **PARTIAL: audit tĩnh hoàn tất; còn xung đột tài liệu và live NOT_VERIFIED.**

## Git và phạm vi

Repository thực tế: D:/StudyMaterials/HK7/Final_Project/ETL (đã chuyển khỏi đường dẫn workspace cũ).
Worktree sạch trước khi tạo nhánh. Nhánh chore/etl/erd-contract-audit được tạo từ feat/etl/architecture-refactor tại 9643c6b. Reflog sau đó ghi fast-forward từ pull origin main lên 18bad82; thao tác này không thuộc các lệnh audit. Đã đối chiếu 9643c6b..18bad82: cùng nội dung file. Baseline hiện tại trong snapshot là 18bad825993e01a552fb5db0a1aa1246cce74c7a; không reset hoặc hoàn tác thay đổi Git bên ngoài.

Đủ 8/8 tài liệu trong TAI_LIEU_DOI_CHIEU; SHA-256 nằm trong contract_snapshot.json. Không sửa tài liệu gốc. Hai stash được giữ nguyên:

- 21fe6ab94afb13221855a51d871540a2d46a156e
- ad07c44d5b6ee5d8efb51c666b133d7f9cddec4d

Chỉ thêm báo cáo/script audit; không sửa schema, migration, handler, Frontend hoặc cấu hình. Không mở/in nội dung .env, không kết nối DB, chạy SQL, Alembic, Docker, worker; không commit/push/xóa stash.
Ngoại lệ kiểm thử được ghi nhận: lần collection pytest đầu import main.py làm Settings tự nạp .env ngầm. Không hiển thị/lưu giá trị bí mật hoặc chạy lifespan/API/DB. Đã chạy lại toàn bộ test chọn lọc sau khi đặt Settings.model_config['env_file']=None trong process trước collection; không sửa file cấu hình. Script audit không import main hoặc khởi tạo Settings.
Đã thông báo xung đột B01/B02 trước khi tiếp tục ghi báo cáo; không âm thầm chọn một phía hoặc triển khai sửa đổi tương ứng.

## Baseline và artifact

| Tầng | Kết quả tĩnh | Thay đổi |
| --- | --- | --- |
| SOURCE PG | 15 bảng, 145 cột, 22 FK, 7 enum; 145/145 trường và 7 enum khớp Excel | Không |
| Master PG | 10 bảng nghiệp vụ + 8 runtime = 18 bảng, 233 cột | Không; chỉ đề xuất |
| MongoDB | Đúng 5 collection contract, 6 index nghiệp vụ, target embedding 384 | Không |

MATCH_STATIC chỉ so sánh SQL file với ô Excel: kiểu, nullable, PK, FK và UNIQUE được mô tả. Không chứng nhận live catalog. Parser chỉ phục vụ cú pháp schema_v1.sql hiện tại. Trigger/CHECK/index xét riêng. Ảnh ERD đã xem trực quan theo bảng và quan hệ; chữ nhỏ không thay thế bằng chứng ô Excel.

- [source_erd_contract_matrix.csv](source_erd_contract_matrix.csv): từng trường, Excel!ô, SQL:line và khác biệt.
- [source_enum_contract_matrix.csv](source_enum_contract_matrix.csv), [source_relation_inventory.csv](source_relation_inventory.csv): enum, hướng/cardinality 22 FK.
- [master_mapping_matrix.csv](master_mapping_matrix.csv): toàn bộ 233 trường, nguồn gộp hoặc ETL sinh, ERD_10/RUNTIME_8.
- [mongo_contract_matrix.csv](mongo_contract_matrix.csv): 45 trường Excel, 11 extension runtime; một enum cần quyết định.
- [migration_trigger_inventory.csv](migration_trigger_inventory.csv), [trigger_inventory.csv](trigger_inventory.csv): 18 revision, SQL và 32 trigger dự kiến.
- [endpoint_inventory.csv](endpoint_inventory.csv), [endpoint_flow_matrix.md](endpoint_flow_matrix.md): 40 endpoint và luồng qua các lớp.
- [contract_snapshot.json](contract_snapshot.json): hash 8 tài liệu và implementation schema/revision, constraints/index ORM, validators, chains và live status.
- [blocking_decisions.md](blocking_decisions.md): severity, bằng chứng và điểm chờ xác nhận.

Inventory code: src/data_sync_etl/{api,application,domain,ports,adapters,db,workers,cli}, composition.py; frontend/src/App.tsx, components/layout, pages/{library,system,search,experiments} và service API.
Đã rà alembic.ini, alembic_source.ini, docker-compose.yml, pyproject.toml, .env.example; không đọc credential/.env.

## SOURCE — bảo toàn hợp đồng

15 bảng: id_counters, education_levels, grade_levels, subjects, providers, resource_types, roles, users, learning_resources, resource_grade_levels, resource_subjects, resource_versions, content_units, ocr_jobs, ocr_page_tasks.

PK ghép counter và hai bảng nối giữ nguyên. FK child→parent ON DELETE RESTRICT; hai self-FK version/ContentUnit là quan hệ cây, không xóa để “gỡ vòng”.
ocr_jobs.resource_version_id UNIQUE đúng Excel ocr_jobs!F5/I5: một job SOURCE/version; không thay bằng partial unique của Master.

Các đề xuất từ báo cáo cũ về UNIQUE(resource,version), UNIQUE(job,page), UNIQUE(version,sequence), anti-cycle, primary subject, grade consistency **chưa phải defect SOURCE được chứng minh**. SELECT tìm nghi vấn không phải phê duyệt thêm DDL.

ID SOURCE dùng 12 prefix + MMYY + 5 chữ số, do ứng dụng/counter cấp; không đổi ID ETL hoặc dữ liệu lịch sử để ép cùng định dạng.
B01: QUY_TAC_TAO_MA_POSTGRESQL.xlsx, ID Counter!A4 ghi las_seq; Source Excel và SQL dùng last_seq. Đề xuất xác nhận typo tài liệu, chưa sửa gì.

Bằng chứng: alembic_source/schema_v1.sql:1; CSV từng trường; src/data_sync_etl/domain/source_ids.py:1 và application/source_ids.py:1.

## Master — mapping và khác biệt

| Master | SOURCE / nguồn tạo | Phân loại |
| --- | --- | --- |
| master_learning_resources | learning_resources + providers (provider_*) + resource_types (resource_type_*) | Giữ gộp snapshot |
| master_users | users + roles (role_*) | Giữ; hashed_password chỉ PG, không Mongo |
| master_grade_levels | grade_levels (grade_*) + education_levels (education_*) | Giữ tham chiếu education logic |
| subjects, resource_subjects, resource_grade_levels | subject và hai N-N theo ID | Giữ FK vật lý |
| resource_versions | Phiên bản ETL; source_file_imports nối source_version_id→target_version_id | Không phải bản sao 1:1 |
| content_units, ocr_jobs, ocr_page_tasks | ETL sinh nội dung/job/task | Không nhập nguyên lịch sử OCR nguồn |

Các khác biệt cần quyết định, chưa sửa schema:

1. Ảnh Master nối created_by→master_users, ORM hiện String(30) nullable, không FK (db/models.py:57). Cần preflight orphan và thứ tự sync trước khi thêm FK.
2. subjects.grade_id Master nullable (db/models.py:134), SOURCE NOT NULL. Cần chốt nghiệp vụ LOCAL trước khi thu hẹp.
3. grade_code ORM/DTO dài 50 (db/models.py:101), SOURCE/ảnh Master dài 30: mở rộng không mất dữ liệu nhưng cần thống nhất tài liệu.
4. source_origin/source_state_hash/source_missing_at và master_users.deleted_* là extension, không tự xóa vì ảnh không vẽ.
5. grade_mongo_id, version/unit.mongo_id đã có qua 0014: mapping logic xuyên DB, không tạo collection grade/version.
6. ContentUnit Master có same-version parent FK/check/anti-cycle trigger; task UNIQUE(job,page); OCR job chỉ unique khi PENDING/RUNNING. Giữ bảo vệ runtime, không áp ngược SOURCE.
7. adapters/source/postgresql.py:266 không đọc parent_version_id/actor duyệt nguồn. application/source_file_import.py:253 chọn version Master trước làm parent, sinh ID/storage đích. Cây SOURCE phân nhánh có thể không bảo toàn nguyên lineage; chốt phạm vi Prompt 02.
8. application/list_resource_lookups.py:74/:102 rút provider/type LOV từ snapshot resources: danh mục SOURCE chưa được học liệu dùng có thể vắng. Cần chốt coverage, không tự thêm bảng.

Code paths trên tương đối src/data_sync_etl/.
8 runtime phải giữ: sync_checkpoints, sync_runs, sync_auto_config, sync_auto_streams, ocr_page_results, etl_stage_runs, content_unit_texts, source_file_imports.

## Mongo — giữ 5 collection

users: extension hồ sơ; learning_resources: description/tags/authors nhúng; ocr_pages: OCR thô; mongo_content_units: text/markdown/keywords chuẩn hóa; content_bags: túi từ/embedding thứ cấp, không phải nguồn/LOV.
Master.mongo_id là mapping logic; không ép _id user/resource bằng ID SOURCE. Riêng CU dùng ID ContentUnit ETL.

6 index tại adapters/mongo/schema.py:261:

- ocr_pages: ocr_job_page(job_id,page_num), unique; ocr_version_page(resource_version_id,page_num).
- mongo_content_units: units_version_current_page(resource_version_id,is_current,page_from); units_keywords(keywords).
- content_bags: bags_version_type(resource_version_id,unit_type); bags_keywords(keywords).

Không cộng 5 index _id mặc định vào số 6. ContentBag dùng INDEX_EMBEDDING_DIMENSION=384; VECTOR_DIMENSION=256 là legacy (.env.example:128–137).
B02: Excel content_bags!G6 chỉ CHAPTER/LESSON/SECTION, runtime thêm PAGE_TEXT/TXT_TEXT/CSV_TEXT/DOCX_TEXT/XLSX_TEXT/PPTX_TEXT/IMAGE_TEXT. Cần duyệt cập nhật mô tả Fallback; không thu hẹp validator/xóa Bag.

11 extension runtime generation/current/fingerprint/engine... được ghi trong CSV, không tự loại bỏ. Shared metadata mặc định xác minh mapping; identity projection opt-in sau PG COMMIT, không giả lập profile đầy đủ.
Quét code không thấy writer tạo indexed_chunks; installer chỉ tạo contracts. Inventory nhận diện extra collection nhưng không xóa. Live collection/validator/document compatibility NOT_VERIFIED.

## Migration / trigger

SOURCE s0001→s0002→s0003; Master 0001→…→0015: hai chain đơn, mỗi chain một head, không suy ra đã áp dụng live.

- s0001 nạp schema_v1.sql; s0002 cài 26 trigger: 12 timestamp, 8 soft-delete, 2 touch liên kết, 4 touch dependency.
- s0003 preflight trước 6 CHECK: version_number, sequence_no, unit pages/range, task page, task retries, job counts; thay hàm timestamp bằng timestamps_v2.sql.
- Master 0006 cài 5 timestamp runtime và trg_content_parent_guard; 0007 cập nhật hàm created_at bất biến/updated_at không lùi.
- Trigger có DROP trong downgrade revision cài; s0003/0007 phục hồi hàm cũ. Downgrade không mặc định an toàn dữ liệu: nhiều revision DROP bảng/cột.
- Guard: 0006 chặn nullable downgrade có NULL; 0013/0014 chặn mất mapping; 0015 chặn mất ownership. Chưa test downgrade/SQL–ORM parity trên DB test.
- Defect B13: 0005 upgrade tạo ix_subject_grade_active_deleted nhưng downgrade dùng ix_subjects_grade_active_deleted (file 0005_subject_query_indexes.py:10/:38). Do if_exists, có thể bỏ qua việc xóa và để lại index; chỉ ghi nhận, không sửa revision cũ.
- 0010 có data migration tạo stream và tắt auto cũ nếu config tồn tại. Đây không phải SQL read-only; raw revision không nên chạy lặp ngoài version tracking. Chưa đánh giá trên dữ liệu live.
- Mongo CLI thực tế: --check, --apply, --bootstrap-empty, --inventory; không có --install. Bootstrap chỉ DB trống, DB cũ sai phải fail closed.
- SCHEMA_VERSION là nhãn contract, chưa phải lịch sử revision chain. Đề xuất manifest/version/hash ngoài DB cho Prompt 04, không tạo collection thứ sáu.
- Compose có master-migrate/source-migrate và mongo-init --bootstrap-empty; không chạy vì có ghi schema.

Hai AUDIT_*_READONLY.sql, scripts/sql/verify_source.sql và verify_master.sql đã đọc, SELECT-only, chưa chạy. source_reader.sql tạo role/grant, không phải read-only.
pg_constraint/pg_trigger/pg_indexes, revision live, Mongo validators/documents đều NOT_VERIFIED. Không kết nối đích chưa xác nhận chỉ để điền PASS.

## Luồng / rủi ro còn lại

SOURCE là authority metadata; PATCH LOCAL mới được sửa, POSTGRESQL/UNKNOWN fail closed. Giữ ownership/state_hash.
4 stream grades/subjects/users/resources có checkpoint, Full/Incremental/Auto và reconciliation. PG commit rồi Mongo projection không phải distributed transaction.

Local/COURSE_DRIVE là file nguồn. Drive reader dùng drive.readonly, bucket/folder allowlist; chỉ tải, không upload đầu ra Drive.
Import xác minh hash, lưu storage đích; extraction/OCR→postprocess→ContentUnits/text PG→Mongo→ContentBag/384D. Finalizer/worker hiện có kích auto-index; audit không chạy READY.

Search/Context dùng chung SearchAccess; public fail closed vì chưa có public-rights đáng tin cậy. Local demo không phải auth Production.
**P1:** GET content-units tại api/routes.py:884 không gọi SearchAccess; orchestrator chỉ kiểm active/deleted. Cần chốt đây là API quản trị nội bộ hay truy xuất công khai trước Production. Không đổi endpoint ở phase audit.
Evaluation giữ nhãn độc lập/file-store; không sinh Ground Truth từ điểm Search. pyproject.toml lặp python-docx, openpyxl, Pillow, python-pptx: P2 maintenance, không sửa ngoài scope.

## Kiểm tra đã thực hiện

Đặt PYTHONPATH=src, PYTHONDONTWRITEBYTECODE=1 vì editable .venv vẫn trỏ workspace cũ; không cài lại/chỉnh environment.
Khi chạy pytest, vô hiệu env_file trong process trước khi gọi pytest.main; không đọc file .env ở lần nghiệm thu lại. Kết quả chạy chung: 46 passed, 1 deselected.
Lần nghiệm thu lại còn gắn readonly_guard để chặn mọi open(.env) và socket.connect. Script audit mặc định cũng gắn guard này. Có thể tái lập bằng PowerShell:

```powershell
$env:PYTHONPATH=Join-Path (Get-Location) 'src'
$env:PYTHONDONTWRITEBYTECODE='1'
@'
import sys
from scripts.audit_erd_contract import readonly_guard
sys.addaudithook(readonly_guard)
from data_sync_etl.config import Settings
Settings.model_config['env_file'] = None
import pytest
raise SystemExit(pytest.main([
    '-p', 'no:cacheprovider', '-q',
    'tests/unit/test_mongo_foundation.py',
    'tests/unit/test_source_ids.py',
    'tests/unit/test_resource_metadata_ownership.py::test_domain_fails_closed_for_all_non_local_provenance',
    '-k', 'not test_api_can_start_with_mongo_unavailable',
]))
'@ | .\.venv\Scripts\python.exe -X utf8 -
.\.venv\Scripts\python.exe -X utf8 scripts/audit_erd_contract.py --check
```

| Lệnh / kiểm tra | Kết quả |
| --- | --- |
| python scripts/audit_erd_contract.py --write; --check | PASS, 9 artifact tĩnh tái lập |
| ruff check --no-cache scripts/audit_erd_contract.py | PASS |
| pytest -p no:cacheprovider tests/unit/test_mongo_foundation.py -k "not test_api_can_start_with_mongo_unavailable" -q | 19 PASS; 1 deselected |
| pytest -p no:cacheprovider tests/unit/test_source_ids.py tests/unit/test_resource_metadata_ownership.py::test_domain_fails_closed_for_all_non_local_provenance -q | 27 PASS |
| AST parse source/revision/script | PASS |
| git diff --check và whitespace artifact mới | PASS |
| Live catalog/SQL/Mongo schema check, Frontend/E2E/model/OCR/Drive | NOT_RUN |
| Alembic upgrade/downgrade, test fixture container/pg_container/mongo_test_database | NOT_RUN theo ràng buộc |

46 test là pure/mock, không dùng container fixture vì fixture đó chạy migration. Không claim full regression PASS.

## Bàn giao / rollback

Không có DDL/migration/data thay đổi hoặc DB live được sửa. Rollback audit chỉ bỏ artifact/script mới sau quyết định của người dùng, không cần rollback DB; không tự xóa.
B01/B02 cần xác nhận trước khi khóa hợp đồng cuối. Các công việc tiếp theo đề xuất, chưa được thực thi:

- Prompt 02: ownership, lineage, created_by/grade, coverage LOV và preflight Master.
- Prompt 03: metadata extension/mapping xuyên PG–Mongo, không profile giả.
- Prompt 04: versioned manifest/bootstrap/catalog trên DB test riêng, giữ 5 collection.
- Prompt 05: nghiệm thu Local–Drive/OCR/READY/quyền/UI trên dữ liệu test.

Phải đọc prompt thật từng giai đoạn khi được cung cấp; bảng trên không phải phép chạy tiếp.
Suggested commit, chưa tạo: docs(etl): audit source master mongo contracts and migration state.
