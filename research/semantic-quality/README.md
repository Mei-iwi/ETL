# Đánh giá tìm kiếm

Runtime mặc định dùng BoW/BM25, Meaning Bag + BGE-M3 và Hybrid/RRF. Hướng dẫn cài model và cấu hình ở [README gốc](../../README.md).

## Dữ liệu đầu vào

- `query-specification.json`, `qrels.json`: truy vấn và nhãn thử nghiệm.
- `optimization/queries-v1.json`, `qrels-v1.json`, `freeze.json`: tập mở rộng, nhãn và checksum dùng bởi công cụ đánh giá.
- `fixtures/search/`: corpus nhỏ, schema snapshot và ca kiểm thử từ điển; không phải kết quả chạy.

Giữ dữ liệu nhãn để tái chạy. Nhãn chưa được nghiệm thu độc lập; điểm cao trên fixture không chứng minh chất lượng cho mọi kho. Tìm tài liệu liên quan không bảo đảm tài liệu có đáp án; ngưỡng thấp có thể trả nhiều kết quả không phù hợp.

## Lệnh

```bash
# Benchmark từ điển offline, không cần truy cập DB.
python scripts/benchmark_concepts.py --output storage/benchmarks/concepts.json
# BoW/Hybrid trên corpus fixture, dùng model local.
python scripts/benchmark_lexical.py --model Models/bge-m3 --output storage/benchmarks/lexical.json
# Chỉ MASTER/Mongo loopback, READ ONLY; đo service đã làm nóng, không phải HTTP load test.
python -m scripts.benchmark_search_runtime --threads 2 --samples 2 --output .test-tmp/search-runtime-new.json
python -m scripts.evaluate_meaning_context --baseline-ref 80b61ce --output .test-tmp/search-quality-new.json
```

File đích hai lệnh cuối phải mới. Đầu ra có thể chứa đoạn trích riêng tư, lưu ngoài Git tại `.test-tmp`/`storage`. Báo cáo cũ tra trong lịch sử Git; không dùng lại kết quả khi corpus/model/chính sách thay đổi.

[Chunk semantic opt-in](../bge-m3/README.md) là pipeline khác, chỉ bật khi đã backfill và kiểm chứng. Công cụ nâng cao: [optimization](optimization/README.md).
