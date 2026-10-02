# Sẵn sàng thay source adapter

**PRODUCTION_MODE_STATUS: BLOCKED_SOURCE_MAPPING.** Chưa có mapping DB/API vật lý hoặc quyền kết nối.
`SOURCE_ADAPTER=json` tạo JsonFixtureSourceAdapter. `production` fail-fast bằng thông báo tĩnh;
không tạo connection giả, không đoán tên bảng hoặc URL. Factory nằm ở `composition.create_source`.

Trước khi viết adapter thật cần cung cấp:

| Thông tin | Cần xác định |
|---|---|
| Connection method | DB driver/API protocol, môi trường test, TLS, cách cấp credential qua secret store |
| Physical source | Bảng/view/schema thật hoặc endpoint/version API cho từng DTO |
| Field mapping | ID, parent IDs, audit, status, title, role/provider/education snapshot, password-hash policy |
| updated_at | Timezone/precision, clock monotonic, update child khi parent/relation đổi |
| Deletion | Soft delete/tombstone; xóa relation phải phát watermark owner; cách xử lý hard delete |
| Cursor guarantee | Thứ tự (effective_updated_at,id), consistent snapshot, late update/backfill, pagination |
| Quan hệ | Referential integrity, duplicate policy, lượng relations/resource, batch JOIN/prefetch |
| Vận hành | Read-only scope, timeout/retry/rate limit, độ lớn batch, checkpoint recovery và test fixtures |

Implement SourceDataReader + DTO trong ports/source.py; wiring mới chỉ ở composition factory.
Source có thể JOIN/prefetch aggregate để tránh N+1 network requests. Không đưa concrete adapter vào MasterSync.
Master checkpoint/resource relations vẫn commit cùng batch. Full scan so sánh mọi row; incremental chỉ xử lý
cursor lớn hơn watermark. Effective timestamp của fixture bao gồm parent và relations; xóa relation phải tăng
resource.updated_at. Timestamp lùi/sửa field không tăng timestamp cần full sync có chủ đích.

Fixture dùng ijson streaming + top-k batch và cache 256 parent. Trước stream, kiểm tra unique ID/relations
trên toàn tệp bằng SQLite **scratch index trên đĩa**, RAM cache 512 KiB; không phải DB nguồn production,
không thay schema master. Index tạm chỉ chứa ID, đóng connection trước xóa để tương thích Windows.
JSON phải là top-level array; lỗi parsing/duplicate/missing dependency không đưa raw record vào thông báo.
Chi phí scan/preflight là tradeoff của fixture, chưa phải throughput production.
