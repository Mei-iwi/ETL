# Logical resource và physical version

`master_learning_resources.id` là tài nguyên logic ổn định. `resource_versions.id` là file vật lý bất biến.
Không dùng title/tên file làm khóa version. SHA-256 tính streaming từng block 1 MiB, cùng lúc sao chép
vào local object store theo hash. Không load toàn file/PDF vào RAM.

Ingestion kiểm tra resource tồn tại, active và chưa deleted. Sau snapshot, transaction khóa master resource
bằng SELECT FOR UPDATE, kiểm tra lại policy và đọc latest version_number.
Không có version → v1/parent=NULL. Hash bằng latest → NO_CHANGE. Hash khác → vN+1/parent=latest.id.
Cùng bytes với một version cũ nhưng khác latest vẫn tạo version mới: lịch sử ghi nhận một lần revert.
UNIQUE(resource_id,version_number) là lớp bảo vệ DB sau khóa resource.
ULID 26 ký tự, lifecycle INGESTED, timestamps UTC.

Hai ingestion đồng thời trên cùng resource được tuần tự hóa bởi row lock. Hai resource độc lập không cần khóa nhau.
Storage object keyed by hash cho phép dùng chung bytes. Lỗi DB có thể để snapshot không có reference;
không tự garbage-collect vì baseline ưu tiên giữ lịch sử. STORAGE_ROOT là thư mục tin cậy,
không cho người dùng ngoài thay nội dung `_versions` trực tiếp.
