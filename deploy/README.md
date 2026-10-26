# Vận hành Docker

Cài mới, cấu hình `docker.env`, DB, model BGE-M3 1024D và Build/Start: [README gốc](../README.md). Mẫu mặc định dùng localhost, đăng nhập thật và model mount read-only; không tự tải model hay migration khi Start.

## Linux

Sửa đường dẫn trong `docker.env` sang path tuyệt đối Linux. Build image trước khi tải model/tạo secret:

```bash
bash deploy/scripts/etl-docker.sh build
mkdir -p "$HOME/etl/models/bge-m3"
ETL_MODEL_TOOL_IMAGE=etl-portable-backend:local bash deploy/scripts/etl-docker.sh prepare-model \
  "$HOME/etl/models/bge-m3" --accept-model-license \
  --model-id BAAI/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 --dimension 1024
sudo install -d -o 10001 -g 10001 -m 0700 /srv/etl-private/auth
sudo env ETL_AUTH_SETUP_IMAGE=etl-portable-backend:local bash deploy/scripts/setup-auth.sh /srv/etl-private/auth YOUR_ADMIN_EMAIL
bash deploy/scripts/etl-docker.sh network
# Nối DB vào etl-data và điền connections.json trước khi Check.
bash deploy/scripts/etl-docker.sh check
bash deploy/scripts/etl-docker.sh start --workers
```

UID 10001 cần đọc model/secret; proxy key cần group 10001 đọc. Không chmod 777. Đã có model/secret thì dùng lại, không tạo đè. `setup-auth.sh` không sửa `docker.env`; điền email và ba secret path vào file cấu hình.

## Chuyển image sang máy khác

Máy xuất, sau Build cả backend/frontend đúng tag trong `docker.env`:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Export -Archive D:/etl/etl-images.tar
```

Chép `.tar` và `.tar.sha256` sang máy nhận có repo, Docker và `docker.env` phù hợp:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Import -Archive D:/etl/etl-images.tar
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Check
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Start -Workers
```

Image không chứa model, secret, dữ liệu DB hoặc tệp SOURCE. Chuẩn bị riêng các tài nguyên này và sửa path/network trước Check. Dùng cùng tag khi export/import; không cần Build lại trên máy nhận nếu dùng image đã import.

## Các profile

| Nhu cầu | Cấu hình |
| --- | --- |
| DB cùng máy | `DATABASE_MODE=same-host`, DB alias trên `etl-data` |
| Chỉ SOURCE Neon | `DATABASE_MODE=source-neon`, `SOURCE_DATABASE_CA_FILE`, URL TLS trong connections.json |
| DB remote | `DATABASE_MODE=remote`, `DATABASE_CA_FILE`, URL TLS khớp chứng chỉ |
| User MASTER + admin cấu hình | `AUTH_MODE=master_users`, profile mặc định `auth` |
| Auth DB riêng | `AUTH_MODE=db_users`, launcher `-AuthProfile db-auth` hoặc `ETL_AUTH_PROFILE=db-auth` trên Bash |
| LAN/Internet | Profile `auth-tls`/`db-auth-tls`, domain, chứng chỉ, cookie secure; xem [auth setup](AUTH_DATABASE_SETUP.md) |

Không bật localhost bypass cho LAN/production. [DB riêng](DATABASE_SETUP.md), [MASTER users](AUTH_MASTER_USERS_SETUP.md), [Google Drive](DRIVE_SETUP.md).

## Cập nhật

Code: Build → Start -Workers. Nội dung secret/JSON thay đổi: Stop → Check → Start -Workers. Đổi model/dimension cần kiểm tra schema và tái lập vector theo quy trình maintenance có backup.

`Stop` giữ container/volume; không dùng `down -v`, prune hoặc xóa dữ liệu để chữa lỗi. Bằng chứng kiểm thử sinh mới lưu ngoài Git trong `.test-tmp`/`storage`. Máy đích cần kiểm tra lại model offline, kết nối, API và cả ba worker; kết quả máy khác không thay cho nghiệm thu tại máy đích.
