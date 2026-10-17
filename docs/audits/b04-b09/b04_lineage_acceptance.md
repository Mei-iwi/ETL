# B04 — Bảo toàn cây phiên bản SOURCE

Phê duyệt mới của người dùng ngày 2026-10-17 thay thế HOLD_CURRENT_BEHAVIOR cho B04; chỉ code/hợp đồng đọc/kiểm thử, không phê duyệt schema hoặc sửa lịch sử. decisions.json và contract_snapshot.json giữ nguyên.

## Preflight

Nhánh gốc main, BASE_SHA/HEAD dc3b8f2865c6fe3cb715c95ef49f98a6f32b19ff (merge PR61); checkpoint ebd1780 tồn tại và là ancestor. Stash list thực tế trống, không có hai stash trong báo cáo lịch sử. LibraryCreatePage.tsx đang có 511 dòng thêm/194 dòng xóa và LibraryCreatePages.module.css chưa tracked; diff gốc có whitespace tại dòng 583. Người dùng cho phép tiếp tục; tạo worktree riêng .worktrees/b04-b09 trên nhánh fix/etl/source-lineage-full-catalog. Không sửa hoặc đưa thay đổi gốc vào changeset này.

Đã đọc final_report.md, lineage_decision.md, phase_g4.md, decisions.json và contract_snapshot.json; đối chiếu code và SOURCE schema_v1.sql/Master ORM. Bộ tài liệu TAI_LIEU_DOI_CHIEU không có trong checkout. Tất cả file schema vật lý, migration, trigger, manifest Mongo và frozen register vẫn khớp cả Git diff lẫn SHA256 baseline: xem schema_preservation.json.

## Đường đi và thay đổi

Đường đi đã xác minh trước sửa: PostgreSQLSourceAdapter.version_page; ReadOnlySourceVersionPort; SourceFileImportService._discover/_import/_fail/retry; SQLAlchemyRepository.claim_source_import/source_import_page; SourceFileImportWorker; API sync-files list/retry và frontend; trusted ingestion và LOCAL manual-create riêng biệt. Test có sẵn: test_source_file_import.py, test_source_google_drive.py, test_lineage_diagnostic.py, source_postgres/test_source_versions.py, source_postgres/test_file_import_flow.py.

| Trước | Sau / test nghiệm thu |
|---|---|
| Reader thiếu parent_version_id | version_page + exact get_version và DTO TypedDict mang parent thực |
| Lấy max version làm cha | Mapping source_system/source_version_id, cùng resource, target tồn tại/khớp |
| V1 → V2 → V3 dù SOURCE phân nhánh | V1 → (V2,V3), hai target child cùng parent target V1 |
| V2 đến sau V3 bị từ chối | Giữ số version SOURCE, chỉ chặn trùng (resource_id,version_number) |
| Con đến trước cha vẫn có thể được nhập sai | PARENT_PENDING, không copy bytes hoặc tạo target trước dependency |
| Không phân biệt parent SOURCE sai | MISSING / RESOURCE / POLICY / CYCLE / MAPPING có mã riêng |

Ví dụ fixture: source-version-1 root → target T1; source-version-2.parent=source-version-1 → T2.parent=T1; source-version-3.parent=source-version-1 → T3.parent=T1. T1/T2/T3 là ID Master mới được tạo, không dùng ID SOURCE làm target. Test assert bằng ID target thực trả về, không chỉ count/HTTP200. Thứ tự nhập 1,3,2 vẫn tạo đúng cây và số 1,3,2; null SOURCE parent luôn null target parent.

Đã bổ sung xác minh dependency trước đọc bytes, kiểm tra lại parent trước đăng ký target và dưới transaction/UoW/resource lock; lease/claim token và unique constraints cũ vẫn bảo vệ race/idempotency. Hai worker nhập siblings đồng thời vẫn cùng parent, lặp claim không tạo duplicate. Hash/number/resource SOURCE thay đổi bị chặn. Khi parent SOURCE của mapping đã thành công đổi, đánh dấu SOURCE_VERSION_MUTATED và giữ nguyên target/parent lịch sử; không âm thầm sửa cây.

## Retry và giới hạn

Tái sử dụng settings hiện có: mặc định 3 lượt tự động; backoff 30s × 2^(attempt-1), cap 3600s, jitter 0–25%. PARENT_PENDING → RETRY_WAIT → RETRY_EXHAUSTED, next_retry_at null khi terminal; không vòng nóng. Manual retry qua cơ chế admin bảo vệ, mặc định tối đa 3 chu kỳ, mỗi chu kỳ revalidate parent. Parent tới sau terminal cần manual retry; không reset attempts vô hạn. Không thêm status/enum/cột; các error code đều trong String(50).

Parent SOURCE mất: PARENT_SOURCE_MISSING; khác resource: PARENT_SOURCE_RESOURCE; disabled/deleted/resource hoặc status sai: PARENT_SOURCE_POLICY; self/cycle: SOURCE_LINEAGE_CYCLE. Mapping khác origin bị bỏ qua; khác resource/target thiếu/hash-number không khớp: PARENT_MAPPING_INVALID; mapping FAILED/RETRY_EXHAUSTED: PARENT_MAPPING_FAILED. Mapping chưa hoàn tất: PARENT_PENDING. IMPORTED/PROCESSING biểu thị file mapping đã commit; OCR completion độc lập, không bắt con chờ OCR cha. Ancestry có safety bound 128; vượt bound SOURCE_LINEAGE_DEPTH_LIMIT, cần can thiệp, không suy đoán cha.

Bytes vẫn theo publication content-addressed cũ; lỗi trước dependency không copy. Nếu lỗi xảy ra sau publication (ví dụ SOURCE đổi trong lúc copy), rollback mapping/target sạch; bytes dùng chung theo hash được giữ để retry, không xóa bytes có thể đang dùng bởi version khác. Regression crash/retry bảo vệ quy tắc này.

## Test plan và bằng chứng

Tám test mới chạy trên code cũ trước runtime edit: 8 FAIL, 5.30s, tái hiện đúng lỗi root/nhánh/cha. Hai PostgreSQL diagnostic lịch sử được chuyển thành assert nghiệm thu cây đúng, giữ snapshot báo cáo lịch sử nguyên vẹn. Unit mới và cùng assertions trên isolated PG bao gồm root/branch/late/version order; pending/exhausted/manual recovery; missing/cross/self/cycle/policy; mapping khác origin/resource/failed/pending/orphan; mutation; concurrency/fencing; disabled/deleted SOURCE; mutation trong lúc copy/rollback. Hồi quy import/lease/hash/Drive/format/OCR/index/search/lineage API và LOCAL/UNKNOWN policy giữ các negative test.

Các batch trước kết quả cuối (có overlap, không cộng): 97 PASS/4 SKIP (31.85s, tool OCR tùy chọn), 12 PG PASS (14.03s), batch B04/B09/import/Drive 34 PASS (39.66s), latest focused B04 unit 17 PASS (23.95s). Một batch chạy song song PG đã lỗi advisory-lock do cùng database test; đã dừng kiểu chạy đó và chạy integration tuần tự. Không xem lỗi harness hoặc diagnostic cũ là PASS.

Lệnh: repository .venv với PYTHONPATH=worktree/src; scripts/run_b04_b09_tests.py tắt repo dotenv; pytest -p no:cacheprovider -q --tb=short tests/unit; integration dùng TEST_DATABASE_URL/TEST_SOURCE_DATABASE_URL/TEST_MONGO_URI trỏ container test có label, random loopback ports và tmpfs. Xem báo cáo cuối cho số duy nhất của full run; không cộng các batch chồng nhau.

## Historical và cổng commit

scripts/sql/verify/b04_lineage_readonly.sql chỉ SELECT, phân tách rõ SOURCE/Master connections; cung cấp source parent và mapped target parent để so sánh offline, không JOIN cross-database hoặc repair. Chưa chạy diagnostic trên Dev: NOT_RUN. Lịch sử cần sửa: PENDING_DATA_REPAIR, yêu cầu backup/phê duyệt riêng. Không sửa target_version_id hoặc parent_version_id có sẵn trên Dev.

Static governance đang BLOCKED vì thiếu bộ tài liệu tham chiếu gốc, không tạo file giả hoặc bỏ test. Chưa commit B04 khi gate chưa PASS. Full regression đang được cập nhật; mã nguồn mới nằm riêng trong worktree để review.


## Kết quả nghiệm thu cuối

Implementation B04 đã kiểm thử; trạng thái tổng thể PARTIAL / cổng commit BLOCKED bởi tài liệu governance thiếu. NOT_COMMITTED; HEAD vẫn dc3b8f2865c6fe3cb715c95ef49f98a6f32b19ff. Không stage hoặc commit khi gate chưa PASS.

Full unit trên implementation cuối: 1.018 PASS, 1 FAIL (chỉ governance thiếu ERD_SOURCE_GOC_15_BANG.jpg), 1 SKIP (Windows không cho tạo symlink), 368.50s. Full integration cô lập: 233 PASS, 1 historical XFAIL 0005, 4 deselected, 0 FAIL/SKIP, 305.42s. Không cộng các batch trước. Case nghiệm thu tự phục hồi a-child(version=3) / z-parent(version=1), khác thứ tự quét ID và version, được bổ sung sau lúc full suite đã collection; focused B04 unit 19 PASS (29.05s) và focused PG cuối kiểm tra riêng, không cộng vào counts full suite.

Ruff --no-cache và git diff --check PASS. Full governance command đã chạy và FAIL do thiếu bộ 8 tài liệu gốc; raw SHA256 mọi file schema vật lý/manifest/frozen register vẫn khớp baseline. Isolated schema test xác minh SOURCE 15 / Master 18 / Mongo 5. Xem final_report.md cho commands, exclusions và focused PG cuối. Chưa chạy diagnostic hoặc repair lịch sử trên Dev.
