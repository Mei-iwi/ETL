# Security baseline

Rà soát 02/10/2026: dùng `python scripts/package_source.py` tạo gói bàn giao allowlist.
Gói không chứa `.env`, `.venv`, cache, egg-info, test DB hay storage. Giữ `.env` và storage đang dùng
trong workspace local để không làm hỏng kết nối/lịch sử; không gửi nguyên thư mục workspace.
ASGI middleware bắt lỗi không dự kiến trước khi server có thể log traceback. Domain error công khai
là thông báo tĩnh, source validation và OCRResult repr không in dữ liệu đầu vào/text.

- hashed_password tồn tại theo ERD. Source DTO dùng SecretStr; repository get/find/insert loại field sensitive.
  Chỉ sync_upsert truy cập để so sánh bên trong adapter. Không có API trả master users hay auth service.
- Log JSON dùng allowlist field, không truyền exception raw, DTO, password/hash/token/secret hoặc document body.
  SQLAlchemy hide_parameters=True, engine logging WARNING. API validation không phản chiếu input body.
- Local storage resolve path và kiểm tra nằm dưới STORAGE_ROOT, từ chối absolute path, drive/UNC,
  `..`, backslash/alternate data stream. Root phải thuộc quyền kiểm soát của operator; không dùng thư mục
  mà người dùng không tin cậy có thể thay symlink trong khi process hoạt động.
- `_versions` và `_ocr` là object/internal artifacts. OCR text/body là dữ liệu tài liệu nhạy cảm;
  cần quyền filesystem/DB phù hợp, backup và retention theo môi trường triển khai.
- `.env` bị gitignore và dockerignore. `.env.example` chỉ có placeholder; fixture hash là dummy không dùng đăng nhập.
  Compose buộc operator cấu hình password và chỉ publish PostgreSQL lên localhost.
- Admin API mặc định tắt, chưa có authentication/authorization. Chỉ bật ở localhost hoặc mạng dev tin cậy;
  chỉ dùng bind 127.0.0.1 cho local dev; không publish LAN/Internet. Chưa hỗ trợ remote admin.
  Startup warning khi bật, validation từ chối field dư và không echo input. CLI lỗi trả code 1;
  sai cú pháp trả 2 với message tĩnh. Runtime container non-root, admin mặc định false.
- Không có adapter tới hệ thống nguồn thật, không lưu URL/credential nguồn chưa được cung cấp.
