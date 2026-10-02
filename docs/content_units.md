# Content Units và hậu xử lý

Chọn job mới nhất theo (created_at,id), rồi yêu cầu COMPLETED/COMPLETED_WITH_ERRORS.
Không fallback về job terminal cũ nếu job mới nhất còn chạy. Duyệt page_num từ 1..N,
đọc từng result, normalize Unicode NFC, space ngang, line endings; giữ paragraph boundary bằng dòng trắng.
Không dịch, sửa chính tả hoặc ghép hyphen chưa chắc chắn. Không có heuristic heading ở baseline.

Mỗi trang có normalized text không rỗng sinh PAGE_TEXT. Sequence liên tục theo thứ tự trang thành công có text;
page_from=page_to=page_num giữ provenance kể cả có khoảng trống trang lỗi. title/parent_unit_id NULL,
review UNREVIEWED, retrieval_eligible=false. Trang rỗng không có unit.
Bảng `content_unit_texts` lưu body và FK đến OCR result; normalized_text cũng được lưu trong page result.

Khóa resource_version tuần tự hóa postprocess. Upsert theo (version, sequence_no), không đổi unit ID/review
nếu body và provenance không thay đổi; nếu thay đổi thì reset review và eligible. Xóa trailing baseline units
nếu desired ít hơn. Một transaction bao trùm normalized results, units, body và stage completion.
Parser có thể fail sau khi đã ghi một vài trang; rollback vẫn khôi phục toàn bộ units cũ.
Không giữ toàn văn document trong list; một page text ở mỗi vòng xử lý.

Mỗi lần chạy có etl_stage_runs riêng để audit retry. Fingerprint gồm phiên bản thuật toán, page status/order
và digest text normalized. Không có queue postprocess nên không ghi mới postprocess_enqueued_at.
Giữ nguyên schema và giá trị legacy nếu đã có; không diễn giải giá trị cũ là enqueue/completion hiện tại.
etl_stage_runs.started_at/finished_at/status là nguồn trạng thái thực thi chính xác.
Coverage gồm successful_pages, failed_pages, total_pages. COMPLETED_WITH_ERRORS cho phép partial content,
trả coverage và log PARTIAL_COVERAGE; nếu không có trang text hợp lệ thì count=0, không giả vờ đủ dữ liệu.

Giới hạn: chưa có UI review, heading/section/table reconstruction; chưa có policy bật retrieval_eligible.
