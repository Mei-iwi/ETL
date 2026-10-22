# Đăng nhập MASTER và admin riêng

`AUTH_MODE=master_users` dùng chung form email/mật khẩu trên `/login`, không có danh sách chọn loại tài khoản. Backend tự xác minh:

- **Người dùng học liệu**: email/password SOURCE→MASTER. Role code `ADMIN` đang active được cấp ADMIN ETL khi `ADMIN_ENABLED=true`; các role khác nhận Viewer. Backend quyết định quyền từ dữ liệu MASTER, không nhận role từ giao diện.
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

`ADMIN_ENABLED=false` chặn admin cấu hình riêng và không cấp quyền ADMIN cho user MASTER. LocalAdminBoundary vẫn giữ nguyên: các route cũ thuộc boundary này vẫn có thể trả403 khi cờ tắt. User MASTER có role code chính xác `ADMIN`, account/role active, email verified và dữ liệu đồng bộ còn mới được quản trị khi cờ bật. Đổi role hoặc trạng thái làm phiên cũ hết hiệu lực. Không sửa role trong DB bằng cấu hình phía trình duyệt.

Khi gặp401, kiểm tra log Backend với action `MASTER_LOGIN_PASSWORD_MISMATCH`, `MASTER_LOGIN_STATE_REJECTED` hoặc `MASTER_LOGIN_IDENTITY_NOT_FOUND`. HTTP vẫn trả lỗi chung để tránh lộ tài khoản; không gửi mật khẩu/hash vào chat. Nếu lỗi503, kiểm tra `detail.code`, kết nối MASTER và users sync trước.

Kho cá nhân user MASTER chỉ gồm học liệu PUBLISHED có `master_learning_resources.created_by` đúng ID MASTER của mình. Reader lấy danh sách ID từ quyền sở hữu; Backend kiểm tra lại ở các API. Chủ sở hữu được tìm kiếm BoW/Vector/Hybrid, xem ngữ cảnh, xem/tải/source-info khi tệp CONTENT_READY và chỉ mục READY, kể cả unit UNREVIEWED + retrieval_eligible=false mặc định. Quyền này chỉ cấp cho phiên MASTER của chủ sở hữu, yêu cầu cả created_by và resource_ids khớp, không công khai nội dung cho tài khoản khác, không áp dụng cho REJECTED hoặc các cờ hạn chế/thu hồi. Không sửa review_status/retrieval_eligible trong DB. Nút quản lý không có quyền được ẩn; admin vẫn thấy đầy đủ chức năng được phép.

Phiên đã xác thực được tìm kiếm kho chung và xem ngữ cảnh public, không cần thêm ENV hay resource grant. Tài liệu phải PUBLISHED, active, phiên bản hợp lệ, chỉ mục hiện hành READY và các unit liên quan APPROVED/REVIEWED, retrieval_eligible=true, không có hạn chế quyền/riêng tư/giấy phép. Khi TOÀN BỘ unit (kể cả unit cha) đủ quyền, user được mở chi tiết, xem/tải và source-info; phiên bản không đủ quyền bị lọc khỏi danh sách. Nếu chỉ một phần đủ quyền, kết quả có can_view_file=false để ẩn link mở toàn tệp, vẫn xem được Ngữ cảnh đã duyệt. Không cấp quyền chỉnh sửa/xử lý/quản trị qua quyền đọc public. Quyền nguồn, hash, Source Reference Only và yêu cầu đăng nhập giữ nguyên.

`AUTH_MASTER_RESOURCE_GRANTS` được giữ tương thích với file ENV cũ nhưng không còn cấp thêm tài liệu cho user MASTER. Không dùng email, Mongo ID hoặc ID do trình duyệt gửi để suy đoán chủ sở hữu. Admin riêng vẫn quản lý toàn bộ tài liệu theo quyền hiện có; tài khoản Auth DB riêng (`db_users`) tiếp tục theo RBAC/grants đã cấu hình trong Auth DB.

Thay đổi hash/status/role hoặc tập tài liệu sở hữu sau đồng bộ làm fingerprint phiên đổi, yêu cầu login lại. MASTER ADMIN được Sync/Process/Index/Evaluation và các trang quản trị tương ứng khi cờ bật. Tạo/khóa/reset/đổi password MASTER thực hiện tại SOURCE; `/systems/users` chỉ quản lý account của mode db_users, không chỉnh tài khoản SOURCE/MASTER.

Rollback: `AUTH_MODE=single_admin`, giữ hash/key/email và restart; không xóa DB hoặc volume. SOURCE Reference Only không thay đổi.
