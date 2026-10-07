# ETL Operations Console — Frontend Đồ Án Tốt Nghiệp

Giao diện quản trị và điều hành vận hành (Internal Operations Console) phục vụ module **Data Sync + ETL Master Data**.

Công nghệ frontend:
- **Framework:** React 19 + TypeScript + Vite
- **Styling:** Vanilla CSS Design System (High-contrast dark engineering console theme)
- **Icons:** `@phosphor-icons/react`

---

## 1. Cấu Trúc Hai Workflow Chính Trong Giao Diện

### Workflow A — Master Data Synchronization
- **Màn hình:** `Master Sync`
- **Stream hỗ trợ:** `All`, `Resources`, `Users`, `Grades`, `Subjects`.
- **Thứ tự thực thi FK của All:** `grades → subjects → users → resources`.
- **Chế độ quét:**
  - `Incremental Sync`: Dựa trên con trỏ checkpoint (`last_updated_at`, `last_id`).
  - `Full Sync`: Quét toàn bộ snapshot dữ liệu nguồn.
- **Audit:** Lưu trữ và hiển thị chi tiết số lượng records `read`, `inserted`, `updated`, `skipped`, `failed` cùng snapshot `checkpoint_before` và `checkpoint_after`.

### Workflow B — Learning Resource ETL
- **Nguyên tắc cốt lõi:**
  - `Learning Resource` là thực thể logic trong catalog (`master_learning_resources`).
  - `Resource Version` là file vật lý bất biến (`resource_versions`).
  - Hai khái niệm này được tách bạch hoàn toàn trên giao diện.
- **Các màn hình tương ứng:**
  1. `Learning Resources`: Danh mục tài nguyên logic, thông tin môn học, khối lớp, nhà xuất bản.
  2. `Resource Detail`: Cây lịch sử phiên bản (`v1 → v2 → v3`), thuộc tính lưu trữ, mã băm SHA-256.
  3. `Ingestion Form`: Tiếp nhận file vào Object Storage (`bucket/object_key`). Cơ chế kiểm tra băm:
     - **Trùng hash với latest version:** Trả về `NO_CHANGE`, dừng tạo version mới.
     - **Khác hash:** Tạo version mới `v(N+1)` với `parent_version_id = latest.id`.
  4. `OCR Jobs & Page Tasks`: Quản lý tiến trình nhận dạng từng trang, trạng thái worker, retry (tối đa 3 lần), xem trước văn bản thô (raw text) và văn bản chuẩn hóa (normalized text).
  5. `Workers & Recovery`: Giám sát worker, heartbeat (chu kỳ 120s), cơ chế khôi phục task treo (`recover-stale-ocr-tasks`).
  6. `Postprocess Stages`: Hậu xử lý Unicode NFC, loại bỏ khoảng trắng thừa, tính toán `input_fingerprint` (SHA-256), ghi nhận độ phủ (coverage).
  7. `Content Units`: Đích đến của pipeline ETL. Quản lý các đơn vị `PAGE_TEXT`, sequence liên tục, thông tin xuất xứ (provenance) liên kết với kết quả OCR trang gốc. Cố định `review_status = UNREVIEWED`, `retrieval_eligible = false`.

### Hạ Tầng & Sức Khỏe Hệ Thống
- **Màn hình:** `System & Health`
- Phản ánh trung thực trạng thái backend:
  - `PostgreSQL 16`: Kết nối cơ sở dữ liệu.
  - `FastAPI Web Runtime`: `http://127.0.0.1:8000`.
  - `Source Adapter`: `SOURCE_ADAPTER=json` (active) vs `BLOCKED_SOURCE_MAPPING` (production adapter chưa nối DB thật).
  - `OCR Engine`: `OCR_ENGINE=native` (PyMuPDF native text) vs `BLOCKED_OCR_RUNTIME` (chưa tích hợp Tesseract/PaddleOCR cho scan).
  - `Admin API`: Mặc định `ADMIN_ENABLED=false` (403 Forbidden).
  - Bộ thử nghiệm API tương tác trực tiếp (`Live API Contract Tester`).

---

## 2. Hướng Dẫn Chạy Local

### Bước 1: Cài đặt dependencies (nếu chưa có)
```bash
cd frontend
npm install
```

### Bước 2: Khởi chạy Dev Server
```bash
npm run dev
```
Giao diện sẽ chạy tại: **`http://localhost:3000/`** (hoặc port do Vite cấp phát).

### Bước 3: Build bản Production
```bash
npm run build
```
Kết quả được xuất ra thư mục `frontend/dist/`.

## Thao tác với backend thật

- Khởi động backend trên `127.0.0.1:8000`, bật `ADMIN_ENABLED=true` trong `.env` và restart backend.
- Chọn **Live Backend API** trên thanh kết nối. Để trống API Base URL để dùng proxy Vite; nhấn **Kiểm tra kết nối**.
- **Master Sync**: lần đầu chọn All Streams + Full, sau đó dùng Incremental. Bảng runs ghi kết quả các lần chạy trong phiên; thời gian theo trình duyệt. Backend chưa có API đọc checkpoint/lịch sử đầy đủ.
- **Ingestion**: đặt PDF tại `storage/input/book.pdf`, nhập Resource ID thật, bucket `input`, object key `book.pdf`; Uploaded By có thể để trống. Chưa có upload tệp từ trình duyệt. Hash simulation chỉ hiện trong chế độ mô phỏng.
- Kết quả `CREATED` hoặc `NO_CHANGE` đều có Version ID. Nút **Tiếp tục: tạo OCR job** mở bảng thử API và điền sẵn ID.
- **System Health** có form cho đủ 6 endpoint: health, master sync, ingest, create OCR job, postprocess, ETL status. Xem method/path/body trước khi gửi; kết quả có HTTP status, payload và correlation ID. Các POST thực hiện ghi dữ liệu thật.
- Sau khi tạo OCR job, chạy `etl run-ocr-worker --worker-id worker-1` trong terminal. Chọn API ETL status để cập nhật job, rồi hậu xử lý khi job terminal.
- Nếu đã chạy bằng CLI, dán Version ID vào bước **Xem trạng thái ETL** để đưa phiên bản/job vào giao diện. Nội dung từng trang và danh mục đầy đủ chưa có API đọc; giao diện không tự lấy dữ liệu này.
- Worker/recovery chỉ chạy bằng CLI; các nút mô phỏng tắt trong Live. Chuyển chế độ xóa thông báo và kết quả trong bộ nhớ giao diện, không xóa CSDL. Sau reload, dùng ETL status với ID đã lưu để đọc lại.
