# Giao diện ETL

React, TypeScript, Vite và React Router. Đã có Header/Submenu/Layout chung,
trang tổng quan, kho học liệu, thêm/cập nhật phiên bản, xử lý, lỗi đồng bộ,
trạng thái hệ thống và khả năng xử lý. Các route tra cứu/thực nghiệm/lập chỉ mục
đã khai báo nhưng không xác nhận backend đã hoàn thiện các tính năng đó.

Tại thư mục `frontend`:

```bash
npm install
npm run dev -- --host 127.0.0.1 --strictPort
npm run build
npm run lint
```

Windows Git Bash có thể dùng `npm.cmd`. Vite chạy cổng 3000, proxy `/api`, `/admin`,
`/health` đến backend cổng 8000. Chạy backend và worker theo [README gốc](../README.md).

`/library/source-sync` hiển thị trạng thái đồng bộ **PostgreSQL Master**;
thành công ở đây không xác nhận Mongo metadata hay OCR đã hoàn tất.
Metadata được kiểm tra read-only tại chỗ trong cùng `etl_content` bằng CLI/report riêng,
không copy Mongo nguồn → đích hoặc chạy realtime.
`/library/source-sync-errors` chia hai tab retry được/không retry được.
Trang xử lý mở từ thêm/cập nhật/chi tiết; `CONTENT_READY` chỉ xác nhận OCR/Content Units,
không xác nhận indexing/Search. Hướng dẫn nghiệm thu: [README gốc, mục 5](../README.md).
