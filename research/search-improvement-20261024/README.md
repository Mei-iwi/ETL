# BoW / Vector: tốc độ và ngữ cảnh, 24/10/2026

Nhánh: `feat/search/multilingual-context-performance`. Baseline: `80b61ce`.

## Thay đổi

- Gom đọc MongoDB theo pha đầu/cuối của mỗi request, có giới hạn. Pha cuối đọc lại độc lập; vẫn kiểm tra metadata PostgreSQL, fingerprint nội dung, generation, quyền trên toàn bộ phần tử con và BSON đầy đủ. Không cache quyền giữa các request.
- Bỏ phân tích CRF toàn văn bị bỏ kết quả khi tái tính TF; vẫn kiểm tra tokenizer và tính lại toàn bộ fingerprint/TF từ nguồn hiện hành. Payload lexical không đổi.
- Tính cosine nhiều vector với kiểm tra số hữu hạn, chuẩn hóa an toàn và giữ thứ tự khi hòa điểm.
- Chọn đoạn giải thích gần điểm cosine tốt nhất theo từ khóa/khái niệm, giữ điểm nhận/xếp hạng ở cấp ContentBag. Khoảng mặc định `SEARCH_MEANING_CONTEXT_MARGIN=0.08`; `0` giữ đoạn max-cosine.
- Thêm khái niệm Việt–Anh: cộng phân số, quang hợp, tỉ lệ thuận. Không coi trừ phân số hoặc tỉ lệ nghịch là tương đương.
- `SEARCH_BOW_MIN_COVERAGE=0` mặc định để giữ recall từ hiếm trong câu dài. Mức `0.15` là tùy chọn đánh đổi độ phủ; không bật mặc định sau khi kiểm thử phát hiện có thể bỏ sót.

Không sửa schema, dữ liệu, vector, file nguồn hoặc `.env` đang dùng. Không cần reindex chỉ vì thay đổi này. Không tự commit/push. Chỉ mục sẵn sàng vẫn là điều kiện bắt buộc.

## Đo local READ ONLY

32 phiên bản, 292 ContentBag. Cùng model BGE-M3, CPU 2 threads, service đã làm nóng; baseline 1 mẫu, sau thay đổi 2 mẫu lấy trung vị. Không phải P95, HTTP hay kiểm thử đồng thời. Model khởi tạo không nằm trong số đo; tải máy có thể gây dao động.

| Truy vấn | Phương pháp | Trước (s) | Sau (s) | Số tài liệu trước → sau |
| --- | --- | ---: | ---: | ---: |
| green leaf | BoW | 10.895 | 5.005 | 1 → 1 |
| green leaf | Vector | 7.435 | 5.301 | 2 → 2 |
| How to add fractions with different denominators? | BoW | 6.620 | 4.540 | 0 → 2 |
| How to add fractions with different denominators? | Vector | 8.684 | 5.672 | 4 → 4 |
| quantum cryptocurrency banana spaceship | BoW | 11.013 | 5.388 | 0 → 0 |
| quantum cryptocurrency banana spaceship | Vector | 9.610 | 7.185 | 0 → 0 |

Giảm khoảng 25–54% theo từng mẫu, vẫn cần tối ưu tiếp nếu yêu cầu độ trễ dưới một giây. Không tăng timeout để có số đo này.

## Chất lượng: nhãn thử nghiệm có sẵn

62 truy vấn, gồm 43 có nhãn liên quan trực tiếp và 19 không có đáp án trực tiếp. Nhãn chưa được nghiệm thu độc lập. So sánh trên cùng tập vector hiện hành và cùng chính sách ngưỡng/fallback local; không chứng minh chất lượng cho mọi cấu hình triển khai.

| Chỉ số | Trước | Sau |
| --- | ---: | ---: |
| BoW: truy vấn có tài liệu đúng ở top 1 / 43 | 26 | 27 |
| BoW: truy vấn có tài liệu đúng trong top 5 / 43 | 30 | 31 |
| Vector: tài liệu đúng top 1 / 43 | 36 | 36 |
| Vector: tài liệu đúng top 5 / 43 | 38 | 38 |
| BoW: truy vấn không có đáp án vẫn nhận kết quả / 19 | 14 | 15 |
| Vector: truy vấn không có đáp án vẫn nhận kết quả / 19 | 8 | 8 |

Cải tiến chọn đoạn có kiểm thử riêng cho ngữ cảnh gần điểm; chưa có nhãn đoạn độc lập để công bố tăng độ chính xác đoạn. Từ điển giúp recall nhưng có thể tăng kết quả theo chủ đề khi kho thiếu đáp án. BoW là truy hồi tài liệu liên quan, không phải bộ xác nhận câu trả lời. Không tuyên bố đã giải quyết no-answer.

## Kiểm chứng và tái chạy

- Bộ unit/search/evaluation: **145 PASS**, 77,33 giây; evidence `acceptance-v3.xml`.
- Kiểm thử bổ sung quyền, cache HTTP, launcher env và runtime Docker: **132 PASS**, 161,49 giây; evidence `security-v4.xml`. Đây là kiểm thử cô lập/kiểm tra cấu hình, không phải triển khai lại stack đang chạy.
- Python 3.12/Linux trong image QA hiện có, network none: numeric + context **8 PASS**, 11,85 giây. Chưa build lại image ứng dụng hoặc thử tải HTTP nhiều người dùng.
- Ruff `--no-cache`, YAML Compose/mapping hai biến tìm kiếm và `git diff --check`: PASS.

Lệnh bộ kiểm thử chính (145 bài):

```bash
python -m pytest tests/unit/test_search_bulk_phase.py tests/unit/test_search_phase_consistency.py tests/unit/test_meaning_context_selection.py tests/unit/test_search_numeric_batch.py tests/unit/test_curriculum_concepts.py tests/unit/test_search.py tests/unit/test_lexical_search.py tests/unit/test_context_ranking.py tests/unit/test_meaning_bags.py tests/unit/test_multilingual_meaning_search.py tests/unit/test_evaluation.py -q -p no:cacheprovider
python -m pytest tests/unit/test_search_access.py tests/unit/test_http_search_query_cache.py tests/unit/test_http_cache_shared_writer.py tests/unit/test_http_cache_acceptance.py deploy/tests/test_launcher_env.py deploy/tests/test_runtime.py -q -p no:cacheprovider
```

Lệnh đo chỉ cho phép MASTER/Mongo có hostname loopback, PostgreSQL transaction READ ONLY, không đọc SOURCE, không sửa chỉ mục:

```bash
python -m scripts.benchmark_search_runtime --threads 2 --samples 2 --output .test-tmp/search-runtime-new.json
python -m scripts.evaluate_meaning_context --baseline-ref 80b61ce --output .test-tmp/search-quality-new.json
```

File đích phải mới. JSON chứa đoạn trích riêng tư, chỉ lưu ở `.test-tmp`, không đưa vào Git. Evidence phiên này ở `.test-tmp/search-improvement-20261024/`: `baseline.json`, `final.json`, `quality-final.json` và XML kiểm thử. Các lần thử chính sách chưa được chọn cũng được giữ riêng tại đó.
