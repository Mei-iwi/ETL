# Google Drive: nguồn chỉ đọc

Chạy từ gốc repo. SOURCE chỉ lưu tham chiếu/hash; đọc Local/Drive khi xử lý, xem hoặc tải bằng file tạm, không lưu bản sao lâu dài trong `STORAGE_ROOT`. Bật `SOURCE_REFERENCE_ENABLED=true` và `SOURCE_TEMPORARY_FILES_ENABLED=true`.

**Pull code không mang theo quyền Drive, OAuth token hoặc cấu hình cá nhân.** Mỗi người tự đăng nhập bằng tài khoản có quyền đọc thư mục; không dùng token của người khác. Chỉ dùng Local thì bỏ qua mục này.

## 1. Chuẩn bị Google Cloud và thư mục Drive

1. Tạo/chọn project trong [Google Cloud Console](https://console.cloud.google.com/), bật **Google Drive API**.
2. Cấu hình **Google Auth platform**: Branding, Audience và Data Access. Với tài khoản cá nhân, dùng External; khi ứng dụng ở Testing, thêm email đăng nhập vào **Test users**.
3. Chỉ yêu cầu scope `https://www.googleapis.com/auth/drive.readonly`: đọc metadata và tải file, không sửa/xóa. Scope này có thể đọc các file tài khoản được phép truy cập; ETL giới hạn thêm bằng folder mapping, không phải quyền OAuth giới hạn riêng thư mục.
4. Trong **Clients**, tạo OAuth client loại **Desktop app**, tải JSON và lưu ngoài Git, ví dụ `D:/ETL_Secrets/desktop-client.json`.
5. Nhờ chủ thư mục Drive chia sẻ quyền **Reader** cho email của bạn. Nếu dùng OAuth project của nhóm, chủ project cũng cần thêm email vào Test users; chỉ chia sẻ client JSON qua kênh riêng được phép, không chia sẻ token.

Tham khảo [hướng dẫn OAuth Desktop của Google](https://developers.google.com/workspace/drive/api/quickstart/python). Không cần tạo API key hoặc bật chia sẻ công khai thư mục.

## 2. Cài extra và đăng nhập OAuth

Dừng API/worker trước khi cập nhật dependencies. Chạy từ repository, đã kích hoạt `.venv`:

```bash
python -X utf8 -m pip install -e ".[dev,embeddings,google-drive]"
mkdir -p /d/ETL_Secrets
python scripts/authorize_drive_readonly.py \
  --client-file "D:/ETL_Secrets/desktop-client.json" \
  --token-file "D:/ETL_Secrets/drive-authorized-user.json"
```

Tự đăng nhập và cấp quyền đọc trên trình duyệt. Chỉ tiếp tục khi terminal báo `OAUTH_SUCCESS`. Script tạo credential **authorized_user**, không in token và không ghi database. Đây là đăng nhập Google cho ETL, không phải đăng nhập WebApp.

Token đích phải là file **chưa tồn tại**, thư mục cha đã có và nằm ngoài repository. Nếu cần xác thực lại, chọn tên token mới rồi cập nhật đường dẫn; không ghi đè hoặc gửi nội dung JSON qua chat/Git/log.

## 3. Khai báo folder và kiểm tra chỉ đọc

Lấy `FOLDER_ID` từ link `https://drive.google.com/drive/folders/FOLDER_ID`, không lấy ID của một file. Sửa `.env`:

```dotenv
SOURCE_DRIVE_ENABLED=true
SOURCE_DRIVE_AUTH_TYPE=authorized_user
SOURCE_DRIVE_CREDENTIALS_FILE=D:/ETL_Secrets/drive-authorized-user.json
SOURCE_DRIVE_BUCKETS={"COURSE_DRIVE":"YOUR_FOLDER_ID"}
```

Ý nghĩa các biến Drive trong `.env`:

| Biến | Cách cấu hình |
| --- | --- |
| `SOURCE_DRIVE_ENABLED` | `true` để đọc Drive; `false` để tắt. |
| `SOURCE_DRIVE_AUTH_TYPE` | `authorized_user` khi đăng nhập OAuth cá nhân theo mục 6.2. |
| `SOURCE_DRIVE_CREDENTIALS_FILE` | Đường dẫn tuyệt đối tới token riêng trên máy, lưu ngoài repository. |
| `SOURCE_DRIVE_BUCKETS` | JSON ánh xạ bucket nguồn sang folder ID, ví dụ `{"COURSE_DRIVE":"YOUR_FOLDER_ID"}`. |


`SOURCE_DRIVE_CREDENTIALS_FILE` trỏ tới **token vừa tạo**, không phải Desktop client JSON. Giữ nguyên `SOURCE_FILE_STORAGE_ROOT` và `STORAGE_ROOT`; Local vẫn hoạt động song song.

```bash
python - <<'PY'
from data_sync_etl.config import Settings
from data_sync_etl.adapters.storage.source_google_drive import SourceGoogleDriveFileReader
SourceGoogleDriveFileReader(Settings()).check()
print("PASS: truy cap thu muc Drive chi doc")
PY
```

Lệnh trên chỉ kiểm tra truy cập thư mục, chưa nhập file hoặc ghi PostgreSQL/MongoDB. API/worker cần chạy bằng tài khoản máy có quyền đọc token. Chỉ khởi động worker nhập file sau khi kiểm tra thành công.

## 4. Điều kiện để đồng bộ file Drive

Không tự quét folder rồi tạo học liệu. File phải có tham chiếu hợp lệ trong `resource_versions` của PostgreSQL nguồn:

| Trường nguồn | Giá trị |
| --- | --- |
| `storage_bucket` | `COURSE_DRIVE` — khớp key trong mapping |
| `storage_object_key` | `files/FILE_ID/ten-file-goc.pdf` — đúng tên và đuôi file |
| `source_hash` | SHA-256 thực tế của nội dung file |
| `lifecycle_status` | `APPROVED` theo cấu hình cho phép |


Tham chiếu file và hash cần được xác nhận tại PostgreSQL nguồn theo bảng trên. Không đổi hàng loạt đường dẫn nguồn chỉ để thử Drive. Khi tắt Drive, đặt `SOURCE_DRIVE_ENABLED=false` nhưng **giữ bucket mapping** để không định tuyến nhầm sang Local.

Nếu dùng service account thay OAuth cá nhân: cấu hình `SOURCE_DRIVE_AUTH_TYPE=service_account`, trỏ tới credential service account ngoài Git và chia sẻ folder cho email service account với quyền Reader; không chạy script OAuth Desktop.

Chỉ hỗ trợ file nhị phân PDF/Office/ảnh thực, được phép tải và nằm trong folder đã cấu hình. Không export Google Docs/Sheets/Slides native hoặc đi qua shortcut.

## 5. Docker

Trong `D:/etl/private/app.env`, bổ sung (thay folder ID/token path bằng giá trị thật):

```dotenv
SOURCE_REFERENCE_ENABLED=true
SOURCE_TEMPORARY_FILES_ENABLED=true
SOURCE_DRIVE_BUCKETS='{"COURSE_DRIVE":"YOUR_FOLDER_ID"}'
SOURCE_LOCAL_BUCKETS=[]
SOURCE_DRIVE_AUTH_TYPE=authorized_user
DRIVE_CREDENTIALS_FILE=D:/ETL_Secrets/drive-authorized-user.json
```

Với Docker, credential host dùng `DRIVE_CREDENTIALS_FILE`; overlay mount read-only thành `/run/secrets/drive.json`, tự đặt `SOURCE_DRIVE_ENABLED=true` và đường dẫn container. Không dùng đường dẫn Windows cho biến bên trong container.

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action Check -AuthProfile auth -EnvFile D:/etl/private/app.env -Drive
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action Start -AuthProfile auth -EnvFile D:/etl/private/app.env -Workers -Drive
```

Linux: `export ETL_WITH_DRIVE=true` trước khi dùng launcher Bash. Luôn giữ cùng overlay khi start/check/stop. Docker image có thư viện Drive, không có credential; nếu chỉ có Docker, có thể dùng service account do owner cấp, hoặc chuẩn bị OAuth token trên máy có Python rồi chuyển qua kênh riêng được phép.
