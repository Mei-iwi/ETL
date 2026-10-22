# Đăng nhập MASTER và admin riêng

`AUTH_MODE=master_users` dùng chung form email/mật khẩu trên `/login`, không có danh sách chọn loại tài khoản. Backend tự xác minh:

- **Người dùng học liệu**: email/password SOURCE→MASTER; quyền Viewer. SOURCE ADMIN không tự thành admin ETL.
- **Quản trị viên**: chỉ hoạt động khi `ADMIN_ENABLED=true`, email và mật khẩu khớp secret Argon2id. Nếu trùng email, mật khẩu admin hợp lệ được kiểm tra trước; mật khẩu MASTER khác chỉ cấp Viewer. Trường `provider` của client cũ không quyết định quyền.

Không cần Auth DB mới hoặc thay schema nghiệp vụ. Giữ URL, Origin và các secret đang dùng; thêm/sửa `.env` local hoặc `D:/etl/private/app.env` Docker:

```dotenv
AUTH_MODE=master_users
ADMIN_ENABLED=true
AUTH_DEV_ADMIN_BYPASS=false
AUTH_MASTER_FRESHNESS_SECONDS=120
AUTH_MASTER_PBKDF2_SALT_ENCODING=auto
AUTH_MASTER_RESOURCE_GRANTS={}
```

Local thường ở `http://127.0.0.1:3000/login`, Docker ở `http://127.0.0.1:8080/login`. Restart Backend local sau đổi ENV. Docker cần image mới; dùng profile **auth**, không phải db-auth:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass \
  -File deploy/scripts/etl-docker.ps1 -Action Start -AuthProfile auth \
  -EnvFile D:/etl/private/app.env -Workers -Drive
```

Bỏ `-Drive` nếu stack không sử dụng Drive. Không ghi password plaintext vào ENV.

Reader chỉ SELECT MASTER bằng connection mặc định READ ONLY. Không ghi last_login/password/status vào MASTER; session/rate state ở SQLite Auth riêng. User phải active, chưa deleted/source_missing, role active, email verified, provenance POSTGRESQL. Email chuẩn hóa trùng bị từ chối.

Verifier hỗ trợ dạng đã audit `pbkdf2_sha256$iterations$salt_hex$digest_hex64`. `auto` xác minh cả salt dạng chuỗi UTF-8 và salt giải mã hex, có giới hạn iterations và so sánh digest an toàn. Có thể chọn riêng `text` hoặc `hex` khi đã xác nhận implementation SOURCE. Không đổi hash hoặc reset password. Test vectors của cả hai cách đã qua; chưa xác minh bằng mật khẩu người dùng thật. Nếu SOURCE có pepper/normalization khác, phải xác nhận hợp đồng trước khi bổ sung verifier.

Nếu hơn120 giây không có users sync_run COMPLETED, user login/session bị từ chối; không dùng tuổi checkpoint watermark thay tuổi run thành công. Admin riêng vẫn login được nếu cờ bật và secret hợp lệ. Độ trễ sync vẫn tồn tại, freshness gate không làm cập nhật SOURCE tức thời.

`ADMIN_ENABLED=false` chặn admin cấu hình riêng. LocalAdminBoundary vẫn giữ nguyên: các route cũ thuộc boundary này vẫn có thể trả403 khi cờ tắt. Role ADMIN ở SOURCE không tự nâng quyền ETL.

Khi gặp401, kiểm tra log Backend với action `MASTER_LOGIN_PASSWORD_MISMATCH`, `MASTER_LOGIN_STATE_REJECTED` hoặc `MASTER_LOGIN_IDENTITY_NOT_FOUND`. HTTP vẫn trả lỗi chung để tránh lộ tài khoản; không gửi mật khẩu/hash vào chat. Nếu lỗi503, kiểm tra `detail.code`, kết nối MASTER và users sync trước.

User chỉ đọc học liệu PUBLISHED do mình tạo, cùng resource IDs được cấp thêm rõ ràng; vẫn giữ Search/File/review/retrieval/ownership guards. Cấp thêm bằng cấu hình JSON và restart:

```dotenv
AUTH_MASTER_RESOURCE_GRANTS={"MASTER_USER_ID":["RESOURCE_ID_1","RESOURCE_ID_2"]}
```

Thay đổi hash/status/role/grants sau đồng bộ làm fingerprint phiên đổi, yêu cầu login lại. Không cấp Index/Sync/Admin từ role SOURCE. Tạo/khóa/reset/đổi password MASTER thực hiện tại SOURCE; `/systems/users` chỉ quản lý account của mode db_users.

Rollback: `AUTH_MODE=single_admin`, giữ hash/key/email và restart; không xóa DB hoặc volume. SOURCE Reference Only không thay đổi.
