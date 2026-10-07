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
