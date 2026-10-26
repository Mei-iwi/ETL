# Giao diện ETL

React, TypeScript, Vite và React Router. Cài backend, model, DB và đăng nhập theo [README gốc](../README.md).

Tại thư mục `frontend`:

```bash
npm ci
npm run dev -- --host 127.0.0.1 --strictPort
npm run build
npm run lint
```

Windows Git Bash có thể dùng `npm.cmd`. Vite chạy cổng 3000, proxy API đến backend cổng 8000. Mở `http://127.0.0.1:3000/login`.
