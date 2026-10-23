# Công cụ đánh giá nâng cao

Giữ `queries-v1.json`, `qrels-v1.json`, `freeze.json` làm dữ liệu đầu vào và kiểm tra checksum. Không sửa nhãn để làm đẹp kết quả. Các tập này là nhãn thử nghiệm, cần nghiệm thu độc lập trước khi dùng làm tiêu chuẩn chất lượng.

Các công cụ trong `scripts/`:

| Script | Vai trò |
| --- | --- |
| `export_semantic_quality.py` | Export corpus theo guard và phạm vi được phép |
| `evaluate_semantic_quality.py` | So sánh cosine/rerank trên corpus export |
| `optimize_semantic_quality.py` | Thử cấu hình, tách selection/holdout, sinh kết quả và selection-lock |
| `review_semantic_qrels.py` | Kiểm tra/chuẩn bị nhãn đánh giá |
| `profile_semantic_runtime.py` | Đo chi phí runtime trên corpus offline |

Dùng `python scripts/<tên-script>.py --help` để xem tham số bắt buộc. Cần model, corpus export và audit khớp nhau; không chạy export lên DB đang phục vụ khi chưa được phép. Output chứa dữ liệu riêng tư nên đặt dưới `.test-tmp` hoặc `storage`. Kết quả và selection-lock sinh mới không phải dữ liệu đầu vào mặc định của ứng dụng.

[Hướng dẫn tìm kiếm](../README.md) · [Cài đặt local/Docker](../../../README.md).
