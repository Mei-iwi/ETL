# BGE-M3: pipeline Chunk tùy chọn

Mặc định ứng dụng dùng Meaning Bag/BGE-M3 qua SentenceTransformer. Pipeline Chunk dưới đây dùng FlagEmbedding, là chế độ opt-in riêng; không cần bật để tìm kiếm BoW/Vector thông thường.

1. Cài `python -m pip install -e ".[semantic]"` trong môi trường riêng; Docker đặt `INSTALL_SEMANTIC=true` trước Build.
2. Chuẩn bị model BGE-M3 local và manifest theo [README](../../README.md), revision/dimension phải khớp chỉ mục.
3. Cấu hình `SEMANTIC_MODEL_DIR` (local) hoặc `MODEL_DIR`/`SEMANTIC_MODEL_DIR` (Docker), `SEMANTIC_MODEL_REVISION`, `SEMANTIC_STRATEGY=contextual`, `SEMANTIC_BATCH_SIZE=2`, `SEMANTIC_MAX_TOKENS=1024`.
4. Chỉ đặt `SEMANTIC_ENABLED=true` khi đã kiểm tra schema và backfill bằng quy trình indexing được duyệt. Không dùng vector 384D cũ cho model 1024D.

`SEMANTIC_RERANK_ENABLED=true` bật ColBERT, tăng chi phí CPU/RAM. Giữ mặc định false nếu chưa benchmark. Muốn trở về pipeline mặc định, đặt `SEMANTIC_ENABLED=false`, restart API/worker; giữ dữ liệu cũ để rollback.

Các script `evaluate_semantic_quality.py`, `optimize_semantic_quality.py`, `profile_semantic_runtime.py` nhận corpus export riêng và model local. Xem `python scripts/<tên-script>.py --help` trước khi dùng; không tự export hoặc ghi vào DB Dev/Production. Kết quả lưu `.test-tmp`/`storage`, không commit. [Dữ liệu nhãn và lệnh cơ bản](../semantic-quality/README.md).
