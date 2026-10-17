# Prompt 04 — Baseline và kế hoạch

Baseline main d07cc1c, Prompt03 đã merge; worktree sạch. Nhánh fix/etl/multimedia-content-index-search.
Giữ nguyên SOURCE15, Master18, Mongo5/dimension384, các quyết định schema/lineage/LOV đang chờ; không sửa .env hoặc DB live.

Sequence: imported bytes/hash → OCR job/tasks → extraction/native hoặc Tesseract → POSTPROCESS/ContentUnits+text → MONGO_PROJECTION completed/current generation → CONTENT_READY → INDEX_BUILD PENDING/RUNNING → leaf keywords<=5 + ContentBag đúng root ID/fingerprint/model384 → verified READY → readonly Search/Context → Evaluation cùng corpus.

Gate: completed OCR không đủ CONTENT_READY; projection/PG-Mongo parity bắt buộc. Chunk là view word windows, không collection. Bag là derived root/leaf aggregation, không entity mới.

Bằng chứng trước sửa:
- application/ocr.py:recognize: scan PDF nhận result rỗng từ engine rồi vẫn SUCCEEDED, chỉ lỗi chung ở finalization. Ảnh/native đã chặn EMPTY_EXTRACTED_CONTENT. Sửa kiểm text scan trước succeed, không biến lỗi thành nội dung giả; giữ max retry/pipeline.
- adapters/embeddings.py:prepare/embed: import/constructor được sanitize nhưng encode probe/query nằm ngoài guard. Lỗi inference raw thành EVALUATION_DEPENDENCY_UNAVAILABLE/INDEX_FAILED thay vì blocker runtime chính xác. Sửa normalize exception riêng encode, giữ INVALID_EMBEDDING và không dùng vector giả.
- application/evaluation.py: runtime errors đã được lưu từng observation; aggregate trả null khi không có successful query, không chuyển n/a thành0. Không viết lại score/ranking.
- Dữ liệu kết quả hiện có đọc-only: run01M530H6ZR4CYKCFP9WYZKNR22 COMPLETED, 18/18 mỗi phương pháp; nhãn chưa human-confirmed. Không tái hiện lỗi lịch sử0/18 nên không nhận là đã tìm root cause lịch sử.
- Runtime .venv Python3.13, local model đúng revision/dim384 đã embed thành công; Tesseract vie+eng check PASS. Không có listener8000/8001 tại lúc kiểm tra. Không chứng minh API cũ đã dùng cùng Python.

Thực hiện: regression tái hiện lỗi trên fixture → sửa tối thiểu → unit + PG/Mongo cô lập, real local model/Tesseract offline → nghiệm thu54 Search cùng18 query/k1,3,5 + Context/evaluation → schema governance/Ruff/diff → báo cáo.

Bổ sung bằng chứng trước sửa: OCRPipeline.recognize đọc OpenXML từ destination mà không so source_hash lần nữa. Import đã kiểm hash nhưng file lưu trữ có thể thay đổi sau đó. Thêm kiểm hash trước validator/parser cho ba định dạng Office (một task/document, không hash lại toàn PDF mỗi trang gây O(n²)); sai hash phải dừng và giữ nguyên hash/version. Không sửa file để khớp hash.
Không thay GUI vì không có thay đổi API contract. Dữ liệu fixture không phải tài liệu/ground truth production; OAuth Drive dùng mock.

Bổ sung tương thích lỗi: get_processing_job/source_file_import chỉ trả mã lỗi legacy media, đang che EMPTY_EXTRACTED_CONTENT/SOURCE_HASH_MISMATCH/BLOCKED_OCR_RUNTIME của document. Mở whitelist đúng ba mã an toàn này; map thông báo ngắn trên ProcessingPage hiện có, không đổi layout/API/schema. Không trả raw error_message tùy ý.

## Tiếp tục sau checkpoint hết quota

Giữ đúng nhánh/HEAD và toàn bộ thay đổi chưa commit, không khôi phục docs.rar. Ca mới phát hiện guard scan rỗng gây false-negative cho PDF hỗn hợp có native text hợp lệ: regression tái hiện FAILED thay vì SUCCEEDED. Chỉ sửa điều kiện rỗng khi không có native text hợp lệ; mixed PDF giữ native và ghi ocr_empty=true, không bịa nội dung ảnh. Các test/runtime/report mới được ghi riêng trong report.md; không dùng số liệu checkpoint làm kết quả cuối.

