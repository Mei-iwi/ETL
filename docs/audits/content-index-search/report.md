# Prompt 04 — Nghiệm thu nội dung, chỉ mục, Search và Evaluation

Ngày nghiệm thu: 17/10/2026. **Trạng thái toàn giai đoạn: PARTIAL.**
Code và kiểm thử cô lập được nghiệm thu riêng; không tuyên bố môi trường Dev/browser/Drive thật đã PASS.

## 1. Checkpoint, Git và phạm vi

- Nhánh giữ nguyên: fix/etl/multimedia-content-index-search; HEAD d07cc1c. Prompt03 đã merge.
- Tiếp tục đúng plan.md và các sửa đổi chưa commit, không triển khai lại pipeline.
- Repository: D:/StudyMaterials/HK7/Final_Project/ETL.
- Không switch/tạo nhánh mới, reset, stash apply/drop, commit/push/merge.
- docs.rar bị xóa ngoài phạm vi tác vụ: **giữ nguyên deletion**, không khôi phục, stage hoặc đưa vào suggested commit.
- Stash 21fe6ab94afb13221855a51d871540a2d46a156e và ad07c44d5b6ee5d8efb51c666b133d7f9cddec4d giữ nguyên.
- Không sửa .env, token/credential, model binaries, dữ liệu Dev/Production. Chỉ fixture PG/Mongo mới và file test tạm được ghi.
- Không sửa migration/DDL/trigger/ORM/schema contract; không sửa file tham chiếu hoặc tự phê duyệt FK/LOV/lineage.

## 2. DONE / PARTIAL / NOT_RUN / BLOCKED

| Hạng mục | Trạng thái | Bằng chứng / giới hạn |
| --- | --- | --- |
| Giữ checkpoint, kiểm diff và phạm vi | DONE | Nhánh/HEAD đúng; thay đổi liên quan giữ nguyên, docs.rar không đụng |
| OCR scan rỗng và mã lỗi | DONE | Không SUCCEEDED hoặc sinh ContentUnit/Index khi scan thuần không có text |
| PDF hỗn hợp có native text | DONE | Sửa false-negative mới: giữ native text hợp lệ khi phần OCR rỗng; metadata ocr_empty=true, không bịa phần text trên ảnh |
| SHA-256 Office trước parser | DONE | Hash đọc file đích qua StoragePort; sai hash dừng một lần với SOURCE_HASH_MISMATCH, giữ ID/hash đã lưu |
| Encode probe/query exception | DONE | BLOCKED_EMBEDDING_RUNTIME đã sanitize; giữ INVALID_EMBEDDING cho vector sai, không fallback vector giả |
| API/status thông báo lỗi | DONE | Whitelist ba mã document, giữ tương thích legacy, không trả raw exception |
| Local/Drive mock 20 trường hợp | DONE | Runtime thật Tesseract + model384, PG/Mongo cô lập |
| Index/Reindex/Auto, structured/fallback/mixed | DONE | 6 test model thật chạy riêng PASS; không backfill |
| Search/Context/Evaluation 18×3 | DONE | Cùng fixture corpus, cùng k=1,3,5; đánh giá chất lượng dữ liệu thật NOT_RUN |
| Schema governance, Ruff, FE build/lint | DONE | Không đổi layout, chỉ thêm ba message |
| Điều tra nguyên nhân lịch sử Vector/Hybrid 0/18 | PARTIAL | Không tái hiện trên lỗi runtime tự nhiên hiện tại; mô phỏng outage chỉ xác minh reporting/recovery |
| OAuth Drive, thư mục/file thật | NOT_RUN | Chỉ HTTP mock, không đọc token hoặc gọi Google |
| Browser E2E và server Dev đang phục vụ | NOT_RUN | API test qua TestClient; không khởi chạy/restart writer Dev |
| Chất lượng Search trên Ground Truth chính thức | NOT_RUN | Chưa có bộ nhãn con người xác nhận; không công bố điểm fixture thành chất lượng luận văn |
| Quyết định schema B03/B04/B07/B08/B09 | BLOCKED ngoài scope | Giữ nguyên quyết định chờ duyệt từ Prompt02; không chặn nghiệm thu runtime/test |
| Lỗi downgrade migration0005 | KNOWN DEFECT | Giữ nguyên lịch sử, không tuyên bố đã sửa |

## 3. Luồng và các gate READY

Imported bytes/hash
→ OCRPipeline.create_job / OCRWorker.once
→ native extractor (PDF native/Office/TXT/CSV) hoặc Tesseract (scan/ảnh)
→ OCR result
→ Postprocessor: Master content_units + content_unit_texts
→ ProcessingFinalizer: MONGO_PROJECTION completed/current generation
→ CONTENT_READY
→ IndexingService.enqueue/once/_build
→ keywords tối đa5 mỗi leaf + ContentBag đúng root ID
→ _verified kiểm fingerprint/generation/model/keywords/vector
→ INDEX READY
→ Search/Context chỉ đọc snapshot hợp lệ
→ Evaluation chốt corpus trước/sau và kiểm marker quanh mỗi Search.

OCR job COMPLETED không tự đồng nghĩa CONTENT_READY. File đã copy không tự có Chunk/Bag/Index.
Chunk giữ là word-window view tạm, không thêm collection/entity.
Reindex tạo audit run mới; không xóa run FAILED cũ. Auto chỉ nhận phiên bản/projection mới, không backfill.
PUBLISHED/active/lifecycle và SearchAccess vẫn bắt buộc; public rights chưa xác nhận vẫn fail-closed.
Viewer từ Prompt03 vẫn giữ policy toàn file và READY snapshot; lỗi409 không cấp quyền đọc tệp.

## 4. File đã sửa và chứng cứ

Đường dẫn Backend tương đối src/data_sync_etl/.

| File / vị trí | Thay đổi thật | Regression |
| --- | --- | --- |
| application/ocr.py:147 | SHA-256 DOCX/XLSX/PPTX đọc từ **destination**, trước validator/parser; không gọi snapshot vì snapshot có thể copy file | test_openxml_changed_after_import_is_rejected_before_parser ×3 |
| application/ocr.py:182 | Scan thuần rỗng → EMPTY_EXTRACTED_CONTENT; cleanup đúng ảnh render của claim, không đụng file gốc | test_empty_scan_fails_in_ocr_without_publishing_content ×2 |
| application/ocr.py:199 | Giữ native text hợp lệ ở mixed PDF khi OCR rỗng, đánh dấu ocr_empty; OCR exception vẫn lỗi, không che runtime failure | test_mixed_pdf_keeps_valid_native_text_when_ocr_is_empty; test_mixed_page_keeps_short_native_text_and_ocr_scan |
| adapters/embeddings.py:72 | Bao encode/tolist failure, bỏ chi tiết riêng tư, reset probe readiness; kiểm số chiều/finite/nonzero vẫn qua validate_vector | test_embedding_inference_failure_is_a_safe_runtime_blocker ×2; test_indexing invalid-vector tests |
| domain/format_validation.py:23 | PUBLIC_PROCESSING_ERROR_CODES chỉ bổ sung EMPTY_EXTRACTED_CONTENT, SOURCE_HASH_MISMATCH, BLOCKED_OCR_RUNTIME | Status/regression tests, không trả chuỗi lỗi tùy ý |
| application/get_processing_job.py:30 | Trả mã lỗi document an toàn qua field error_code sẵn có | Regression kiểm error_code; test_processing_job_status |
| application/source_file_import.py:367 | Danh sách lỗi nhập phản ánh mã OCR document an toàn | test_source_file_import / test_source_google_drive |
| frontend/src/pages/library/LibraryProcessingPage.tsx:247 | Map ba thông báo ngắn; không đổi form/layout/router/submenu/CSS | Build/lint |
| tests/unit/test_content_pipeline_regression.py | 8 ca edge regression; các test mới không gọi DB live/model thật | Unit mới |
| tests/integration/source_postgres/test_content_pipeline_acceptance.py | Ma trận20, real model/OCR, Index/Reindex/Search/Context, evaluation54 và injected outage | Integration mới |
| docs/audits/content-index-search/plan.md | Giữ kế hoạch checkpoint | Không sửa lại lịch sử |
| docs/audits/content-index-search/report.md | Báo cáo này | Bằng chứng chạy mới bên dưới |

Không sửa application/indexing.py, search.py, evaluation.py hoặc thuật toán metrics/ranking vì không có bug cần tái triển khai. File viewer/API404/409 đã có trong HEAD từ Prompt03, chỉ kiểm lại, **không tính là sửa mới của Prompt04**.

## 5. Môi trường và bảo toàn schema

- Python gọi trực tiếp .venv/Scripts/python.exe trong repository, version3.13.0. Không dùng executable Laragon ngoài .venv.
- Model local: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2.
- Revision: e8f8c211226b894fcb81acc59f3b34ba3efd5f42; manifest đã đối chiếu.
- Path tương đối: Models/paraphrase-multilingual-MiniLM-L12-v2; chỉ đọc, không đưa binaries vào Git.
- Embedding thật384 chiều, finite, nonzero; model weights local, HF_HUB_OFFLINE=1 và TRANSFORMERS_OFFLINE=1, không tải từ Internet.
- Tesseract: executable Windows đã cấu hình; vie+eng. PATH chỉ bổ sung trong tiến trình unit test, không sửa PATH hệ thống/.env.
- PG16/Mongo8: container test mới với label UUID, port loopback ngẫu nhiên, tmpfs, không mount volume/bind Dev. TEST_* được runner tạo mới, không in URI/password.
- Settings.model_config['env_file']=None và audit hook chặn đọc .env cho test. Unit chặn network trừ Windows socketpair của TestClient.
- Kết thúc mỗi integration run, runner kiểm exact ID/label rồi dọn đúng container mới tạo. Không xóa volume/database live.

| Hợp đồng | Kết quả |
| --- | --- |
| SOURCE15 | Giữ nguyên bảng/cột/FK/enum/trigger/migration, source-origin và quyền sở hữu |
| Master18 | Giữ nguyên10 nghiệp vụ +8 runtime; không tự xử lý pending schema/lineage/LOV |
| Mongo5 | users, learning_resources, ocr_pages, mongo_content_units, content_bags |
| Validator/index/dimension | Không đổi contract, 6 business index, dimension384; không tạo collection thứ6 |
| Legacy VECTOR_DIMENSION256 | Không sửa; ContentBag dùng index_embedding_dimension384 tại composition.py:202/:264 |
| Migration | Không thêm/sửa/apply trên Dev; test chỉ dùng migrations/installer hiện có cho DB test mới |
| Governance | PASS, drift=[], live_applied=false, heads Master0015/Source s0003 |

## 6. Lệnh và kết quả CHẠY MỚI

Không dùng số234 PASS/3 SKIP của checkpoint làm nghiệm thu cuối.

Runner thực tế gọi .venv/Scripts/python.exe -X utf8 - (Python nhận script qua stdin), thiết lập môi trường an toàn trên rồi gọi pytest.main. Các tham số pytest thực tế:

### A. Unit mở rộng

python -m pytest -p no:cacheprovider -q -rs --basetemp <TEMP_TEST>/pytest [14 targets]

Targets:

- tests/unit/test_content_pipeline_regression.py
- tests/unit/test_ocr_readiness.py
- tests/unit/test_multiformat.py
- tests/unit/test_indexing.py
- tests/unit/test_search.py
- tests/unit/test_search_context.py
- tests/unit/test_search_access.py
- tests/unit/test_evaluation.py
- tests/unit/test_evaluation_metrics.py
- tests/unit/test_resource_file.py
- tests/unit/test_source_file_import.py
- tests/unit/test_source_google_drive.py
- tests/unit/test_processing_job_status.py
- tests/unit/test_processing_job_retry.py

**366 passed in176.79s (0:02:56); EXIT0, 0 FAIL/SKIP/XFAIL.** Tesseract ảnh đã chạy thật qua PATH tiến trình, không dùng lại3 SKIP của checkpoint.

### B. Integration riêng runtime cũ

python -m pytest -p no:cacheprovider -q -rP --basetemp <TEMP_TEST>/pytest -k "not react_ui" tests/integration/source_postgres/test_local_embedding_runtime.py

**6 passed, 3 deselected, 1 warning in41.03s; EXIT0.** Ba deselected là browser opt-in, không tính PASS. Cảnh báo thư viện về đổi tên get_sentence_embedding_dimension, không phải lỗi vector.

Evidence:
- REAL_BAG_ACCEPTED keyword_embedding=PASS dimension=384 manual=PASS reindex=PASS (structured/fallback/mixed).
- REAL_AUTO_ACCEPTED ... chunks=21/48/22 auto=PASS duplicate_prevention=PASS.
- Đây là ID/bag test trong database UUID, không phải học liệu Dev.

Checkpoint từng có lỗi fixture source khi chọn chung nhiều module. Chạy module này riêng dùng đúng conftest SOURCE đã PASS; **không sửa/xóa fixture hoặc migration để ép PASS**.

### C. Integration ma trận mới + Index/Search regression

python -m pytest -p no:cacheprovider -q -rP --basetemp <TEMP_TEST>/pytest -k "not react_ui" tests/integration/source_postgres/test_content_pipeline_acceptance.py tests/integration/test_indexing_integration.py tests/integration/test_search_integration.py

ETL_TEST_REAL_EMBEDDING=1, ETL_SEARCH_REAL_MODEL=1; TEST_* chỉ đến container mới.
**28 passed, 2 warnings in132.07s (0:02:12); EXIT0, 0 FAIL/SKIP/XFAIL.** Gồm20 ca ma trận mới,6 Index integration và2 Search integration. Hai warning là deprecation API của thư viện model.

Tổng nghiệm thu mới: **400 PASS** =366 unit +28 integration +6 runtime riêng. Không cộng các lần rerun hoặc54 observations thành54 test.

Chạy riêng Evaluation theo yêu cầu bằng cùng runner an toàn:

python -m pytest -p no:cacheprovider -q -rP --basetemp <TEMP_TEST>/pytest -k "not react_ui" "tests/integration/source_postgres/test_content_pipeline_acceptance.py::test_real_content_to_bags_search_and_evaluation[.docx-local]"

**1 passed, 1 warning in26.93s; EXIT0.** Đây là rerun, không cộng vào400. Log mới xác nhận54 observations, cả3 phương pháp18/0, Recall=null; injected outage/reporting/recovery PASS. Latency trung bình riêng lần này: BoW73.0753ms, Vector93.8236ms, Hybrid92.3665ms. Đây là lần chạy riêng, không trộn với bảng latency của bộ ma trận bên dưới.

| Định dạng | Local / Drive mock | Extractor/OCR thực |
| --- | --- | --- |
| PDF native | PASS / PASS | PyMuPDF native text |
| PDF scan | PASS / PASS | Raster page + Tesseract vie+eng |
| DOCX | PASS / PASS | python-docx; ZIP/Content_Types/word/document.xml |
| XLSX | PASS / PASS | openpyxl, không tự tính công thức; xl/workbook.xml |
| PPTX | PASS / PASS | python-pptx, ppt/presentation.xml |
| TXT | PASS / PASS | UTF-8 native |
| CSV | PASS / PASS | Native CSV với provenance |
| PNG | PASS / PASS | Tesseract thật |
| JPG | PASS / PASS | Tesseract thật |
| JPEG | PASS / PASS | Tesseract thật |

Drive chỉ mock transport, không mock hash/format/PG/Mongo/ContentBag/embedding/OCR ở20 ca này.
Bộ test Index regression cũ có model3D double trong DB test riêng để kiểm lỗi schema/lock/timeout; không dùng chúng làm bằng chứng model384 thật.

### D. Static/frontend

- Ruff check --no-cache trên đúng7 file Python thay đổi: PASS.
- python scripts/check_migration_governance.py: PASS, drift=[].
- npm.cmd --prefix frontend run build: PASS.
- npm.cmd --prefix frontend run lint: EXIT0, không error;5 warning cũ set-state-in-effect ở SystemStatusPage, LibraryResourceDetailPage, LibraryVersionHistoryPage, SystemCapalitiesPage, LibraryUpdateVersion.
- git diff --check: PASS.

## 7. Search/Context/Evaluation — không đánh đồng runtime và Ground Truth

- Mỗi test nguồn/định dạng phải có ContentBag thật, vector384, Index/Reindex READY và Search/Context trả kết quả với keyword của bag; không sửa điểm để ép có kết quả.
- Evaluation dùng cùng18 câu QUERIES, cùng corpus/chỉ mục/k1,3,5 và settings cho BoW/Vector/Hybrid.
- Bộ18 này là **fixture nghiệm thu vận hành**, không phải benchmark chất lượng trên corpus Dev hoặc Ground Truth luận văn.
- Grade0 trong fixture chỉ phục vụ kiểm serialization/nhánh không có positive qrels, ground_truth_confirmed=false và warning LABELS_NOT_HUMAN_CONFIRMED. Không lấy ranking/score làm nhãn, không công bố Precision0 fixture là chất lượng thật.
- Recall/MRR/nDCG không có positive qrels phải null, không giả thành0. Precision provisional theo quy ước hiện tại vẫn có thể số0; không diễn giải thành điểm Ground Truth. Không chỉnh metrics để che n/a.
- Latency lấy perf_counter của mỗi SearchService call, không bao gồm corpus validation; model đã warm trong nghiệm thu này, không dùng làm cold-start/production benchmark.
Kết quả mới từ REAL_EVALUATION_EVIDENCE: status=COMPLETED, observations=54, quality=NOT_RUN, ground_truth_confirmed=false.

| Phương pháp | Thành công/lỗi | TB ms | P50 ms | P95 ms | Recall khi không có positive qrels |
| --- | --- | --- | --- | --- | --- |
| BoW | 18/0 | 115.2318 | 115.5149 | 160.2820 | null |
| Vector | 18/0 | 153.5358 | 143.9062 | 193.2240 | null |
| Hybrid | 18/0 | 148.5122 | 147.2810 | 173.6914 | null |

Các số trên là latency fixture của lần chạy mới, không phải số đo chất lượng hoặc SLA.
Bag counts ở cả2 provider: PDF1, scan PDF1, DOCX1, XLSX4, PPTX5, TXT2, CSV2, PNG1, JPG1, JPEG1; mọi vector384 finite/nonzero và SHA parity=true.
- Injected outage cố ý thay encode bằng lỗi riêng tư: BoW18 thành công, Vector/Hybrid mỗi phương pháp18 lỗi; statusPARTIAL, precision vector/hybrid null, error_code BLOCKED_EMBEDDING_RUNTIME; không lộ PRIVATE_INFERENCE_FAILURE.
- Sau gỡ mock, Hybrid có thể Search lại bằng model thật trên cùng bags, không OCR/reindex/đổi nhãn.
- Run lưu cũ01M530H6ZR4CYKCFP9WYZKNR22 ở checkpoint có18/18 cả3 phương pháp. Không tái hiện lỗi lịch sử tự nhiên0/18 trong runtime hiện tại. **Không kết luận injected outage là root cause lịch sử.**

## 8. Giới hạn, rủi ro, BLOCKED còn lại

1. P1 — Chưa có nghiệm thu API đang phục vụ/browser thật hoặc OAuth Drive thật. Không khởi động writer Dev; cần operator phê duyệt môi trường trước chạy riêng.
2. P1 — Chưa có Ground Truth con người xác nhận. Chất lượng corpus Dev/Production: NOT_RUN; không đưa điểm fixture vào kết luận luận văn.
3. P2 — Native text ở mixed PDF được giữ nếu OCR trả rỗng. metadata ocr_empty không bảo đảm chữ trong ảnh đã được nhận ra; chưa có nhãn đánh giá OCR đầy đủ.
4. P2 — Hash Office được kiểm ngay trước parser, không tạo filesystem lock; không cam kết chống mọi ghi file đồng thời từ tác nhân bên ngoài. Kho content-addressed cần giữ bất biến/quyền ghi đúng.
5. P2 — Model library còn FutureWarning; không đổi dependency/API ngoài phạm vi.
6. P2 — Bộ opt-in runtime cũ cần chạy riêng để fixture SOURCE được thu thập đúng; đã có lệnh6 PASS. Không coi lần chạy chung có fixture error là PASS.
7. Ngoài scope — pending B03/B04/B07/B08/B09 và KNOWN DEFECT0005 giữ nguyên. docs.rar deletion không thuộc tập thay đổi đề nghị commit.

## 9. Rollback và bàn giao

Không có schema/database live cần rollback. Review/revert riêng code của giai đoạn04 nếu cần; không reset worktree, khôi phục docs.rar, xóa stash hoặc xóa bag/file học liệu.
Giữ plan.md, report.md và các test. Chờ người dùng nghiệm thu trước Prompt05; không tự chuyển giai đoạn.

Suggested commit (chỉ gợi ý, chưa tạo):
fix(etl): stabilize multimodal extraction indexing and search evaluation

