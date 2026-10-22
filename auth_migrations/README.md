# Auth Database migration version 1

CLI triển khai: `python -m data_sync_etl.security.auth_db_cli migrate --url-file PATH --confirm-auth-maintenance`.

`0001_initial.sql` là PostgreSQL DDL xuất từ metadata để review. CLI là entrypoint migration được hỗ trợ: kiểm tra DB trống/đúng vùng Auth, chạy DDL trong transaction, seed ba role, ghi `auth_schema_versions=1`. DDL riêng không chứa seed/password, không dùng thay quy trình CLI.

`check-schema` chỉ đọc. Không gọi migration từ startup. Không có downgrade xóa dữ liệu; rollback về single_admin giữ nguyên DB Auth. Hướng dẫn provision/credentials: [AUTH_DATABASE_SETUP](../deploy/AUTH_DATABASE_SETUP.md).
