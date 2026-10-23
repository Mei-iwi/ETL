# No-answer và selective retrieval

**Chưa có ngưỡng đủ an toàn để bật mặc định.** Đã thử ngưỡng tuyệt đối, margin và rule kết hợp;
không thêm mô hình confidence, không coi softmax/cosine là xác suất. Các rule trong
`application/semantic_selection.py` chỉ được evaluator gọi, không nối vào Search API.

Corpus:32 resources/363 Chunk, cùng checksum baseline. Calibration dùng28 validation:
16 câu validation cũ +12 câu mới;20 câu có đáp án và8 không có đáp án. Holdout mới20 câu,
12 có đáp án/8 không có đáp án, đã cố định trước khi tính ranking; cấu hình lựa chọn được lưu
`selection-lock.json` trước khi đọc điểm holdout. Qrels vẫn DRAFT.

Ràng buộc lựa chọn trên validation: Recall@5≥95%, false abstention≤5%; trong các policy đạt
ràng buộc, giảm no-answer FPR rồi ưu tiên recall/P@1 và rule ít hạn chế hơn. Không chọn lại
tham số sau khi xem holdout. Các ngưỡng sau thuộc **subject/grade+content, aggregation mean Top3**,
không dùng chúng cho contextual hoặc legacy: phân phối score/ý nghĩa score khác nhau.

| Phương án chọn trên validation | Ngưỡng tuyệt đối | Margin | Holdout no-answer FPR | False abstention trên12 câu có đáp án | Holdout R@5 |
| --- | --- | --- | --- | --- | --- |
| Không từ chối | 0 | 0 | 100% (8/8) | 0% | 91,67% |
| Absolute | 0,495035 | 0 | 12,5% (1/8) | **25% (3/12)** | 75% |
| Margin Top1−Top2 | 0 | 0 | 100% | 0% | 91,67% |
| Margin Top1−Top5 | 0 | 0,027095 | 75% (6/8) | **8,33% (1/12)** | 91,67% |
| Rule absolute + margin | 0,5 | 0 | 12,5% (1/8) | **33,33% (4/12)** | 66,67% |

Absolute chặn tốt câu không có đáp án nhưng đồng thời bỏ sót nhiều câu có đáp án. Rule kết hợp
được validation chọn margin0, tức không chứng minh kết hợp tốt hơn absolute. Margin Top1−Top2
không hữu ích ở corpus có hai resource ID hợp lệ chứa nội dung tương tự; không được gộp ID
theo tên để làm margin đẹp hơn. Top1−Top5 bớt nhạy với cặp giống nhau nhưng phụ thuộc kích thước
corpus/tập được cấp quyền, vẫn có false abstention. Giữ tất cả thay đổi này ngoài runtime.

Điểm của đáp án, partial và no-answer chồng lấn. Ví dụ mean Top3 của tài liệu có đáp án trong
holdout thấp nhất khoảng0,3394, trong khi query không có đáp án có top score tới0,5139.
Không tồn tại một absolute threshold tách hoàn hảo hai nhóm trong phép thử này.
`score_judgments` trong RESULTS tách `answer`, `partial`, `judged_negative`, `unjudged`,
`no_answer_query_top`. Chưa có resource grade0 được người duyệt xác nhận; bucket explicit-negative
đang rỗng. Không coi hàng trăm resource chưa gán nhãn là negative để huấn luyện hoặc tuyên bố precision chắc chắn.

Các câu bị chặn nhầm gồm tra số liệu bảng và câu hỏi tính toán/tiếng Anh; kết quả theo vi/en/mixed,
hard-negative/ambiguous nằm trong `RESULTS.json.selection`. Rủi ro theo ngôn ngữ không đồng đều;
không chọn ngưỡng riêng cho từng ngôn ngữ từ vài mẫu. Khi thêm tài liệu hoặc đổi quyền của user,
margin và phân phối score sẽ thay đổi; cần validation scope/corpus mới. Chưa có bằng chứng ổn định
trên corpus mở rộng nên không tuyên bố policy này dùng được cho kho lớn hơn.

## Đề xuất API tương thích ngược — chưa triển khai

Search trả kết quả liên quan, không chứng minh câu hỏi đã có đáp án. Giữ nguyên endpoint, `items`,
`total`, pagination và lỗi security/stale. Sau khi có nhãn được duyệt và policy đạt nghiệm thu,
có thể thêm trường tùy chọn `retrieval_assessment`:

```json
{"status":"INSUFFICIENT_EVIDENCE","policy_version":"...","calibration_id":"..."}
```

Ba trạng thái đề xuất: `ANSWER_SUPPORTED`, `RELATED_ONLY`, `INSUFFICIENT_EVIDENCE`.
Hiện chưa có bộ nhãn/policy đủ tin cậy để tự phát cả ba trạng thái. Không phát
`ANSWER_SUPPORTED` chỉ vì cosine vượt ngưỡng hoặc tài liệu có title khớp. `RELATED_ONLY` phải
được UI trình bày là tham khảo, không là đáp án. Khi abstain: lọc trên **toàn bộ tập được phép**
trước pagination, `items=[]`, `total=0`, context không còn truy cập qua kết quả đã loại.
Không sửa `total` thành5 trong khi vẫn giữ32 kết quả; không dùng tài liệu user không được phép để tính margin.

Policy cần gắn với model/revision, input strategy, aggregation, index generation/corpus và scope được phép;
identity không khớp phải từ chối áp dụng calibration. Rollback là tắt policy tùy chọn, giữ index và dữ liệu cũ.
BoW/Hybrid RRF giữ nguyên; không lấy rule Vector tự động cắt nhánh BoW.

## Duyệt nhãn bởi giảng viên

Đã tạo private file `.test-tmp/semantic-quality/optimization/review.jsonl`:11.616 hàng,
32 query×363 Chunk, không chứa score/ranking model. Mỗi hàng có câu hỏi/ngôn ngữ, ResourceVersion/Chunk ID,
nguyên văn dẫn chứng, hash, draft grade/reason và các trường người duyệt nhập.

1. Mỗi người duyệt dùng một bản riêng; có thể export từng query để chia nhỏ khối lượng.
2. Điền `reviewer_id`, `grade` nguyên0..3, `reason`; chuyển `status` sang `REVIEWED` hoặc
   `APPROVED` bằng quyết định của người có thẩm quyền. Agent không tự đặt APPROVED.
3. Giữ nguyên query/IDs/hash/evidence. Dữ liệu chưa đánh giá để `DRAFT`, không tự điền0.
4. Validate các bản để phát hiện sai evidence, thiếu lý do hoặc bất đồng grade giữa người duyệt.
   Hai nhãn APPROVED khác nhau vẫn được báo conflict, không tự lấy trung bình hoặc ghi đè.
5. Chỉ sau khi giải quyết bất đồng và xác nhận đủ coverage mới tạo qrels version mới và holdout mới.

```powershell
python scripts/review_semantic_qrels.py export --query-id H15 --output .test-tmp/semantic-quality/optimization/review-H15-reviewerA.jsonl
python scripts/review_semantic_qrels.py validate --reviews .test-tmp/semantic-quality/optimization/review-H15-reviewerA.jsonl
```

Exporter từ chối ghi đè file review hoặc export full evidence vào source tree. Kết quả hiện tại:
11.616 DRAFT,0 reviewed/approved. Không đưa evidence đầy đủ hoặc thông tin người duyệt riêng tư vào Git.
