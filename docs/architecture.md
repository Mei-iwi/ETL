# Kiến trúc và dependency rule

`api/cli → composition → application → ports/domain`. Adapter triển khai port;
application không import SQLAlchemy, FastAPI hay JsonFixtureSourceAdapter. Domain chứa trạng thái,
ULID, normalization và lỗi nghiệp vụ. Config không kết nối DB lúc import app.

- `db/models.py`: ORM; chỉ adapter persistence và migration tooling biết SQLAlchemy.
- `ports/source.py`: DTO được kiểm tra bằng Pydantic, cursor và source contract.
- `ports/repositories.py`: repository + transactional UnitOfWork dạng context manager.
- `ports/processing.py`: storage, PDF processor, OCR engine và result.
- `application/`: MasterSync, ResourceIngestion, OCRPipeline, Postprocessor, EtlOrchestrator.
- `adapters/`: JSON streaming, SQLAlchemy, local filesystem, PyMuPDF và FakeOCREngine.
- `workers/ocr.py`: process loop và heartbeat thread. Mỗi thread dùng session riêng.
- `composition.py`: nơi duy nhất chọn adapter thật/demo và kết nối các use case.

Repository trả dict, không trả ORM object qua boundary. `get/find/insert` loại field đánh dấu sensitive.
`sync_upsert` so sánh hash ở bên trong adapter, không đưa hash vào giá trị trả về.
UnitOfWork commit khi thoát bình thường và rollback khi có exception; không có commit ẩn giữa aggregate.

Không dùng broker ngoài. PostgreSQL vừa giữ trạng thái công việc vừa cung cấp khóa nhận task.
HTTP sync endpoint chạy trong threadpool của FastAPI; worker dài chạy CLI riêng.
`tests/unit/test_api_migrations.py::test_architecture_dependencies` bảo vệ hướng import.
