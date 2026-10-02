# Source contract

Business dùng `SourceDataReader`, không biết source là JSON, API hay DB. Contract trả `SourceAggregate`
đã join và map sang snapshot target; adapter có thể JOIN/prefetch phía nguồn để tránh N+1 network calls.
DTO cho Provider, ResourceType, LearningResource, User, Role, EducationLevel, GradeLevel, Subject,
ResourceSubjectRelation, ResourceGradeLevelRelation đặt trong `ports/source.py`.

`batches(stream, cursor, batch_size)` trả iterator các batch có thứ tự tăng nghiêm ngặt theo
`(effective_updated_at, stable_id)`. Các tên `iter_learning_resources/iter_users/iter_grade_levels/iter_subjects`
là entry tương ứng. Getter parent/relation có trong port. Cursor là JSON timezone-aware, không dùng offset.
`cursor=None` full scan; truyền cursor là incremental với phép so sánh `>` cả cặp.
Record soft delete vẫn được trả về.

`effective_updated_at` bao gồm thay đổi parent để provider/role/education propagation hoạt động cả incremental.
Adapter thật phải phát lại tất cả child bị ảnh hưởng khi parent đổi. Nếu xóa relation, cập nhật watermark owner;
xóa hard record phải chuyển thành tombstone. Timestamp không được đi lùi; thay đổi sau checkpoint cần watermark
mới hơn cặp checkpoint. Chỉnh field mà không tăng timestamp chỉ được full scan phát hiện.

JSON fixture có 10 file trong `fixtures/source/`: hai providers, hai resource_types, ba resources,
ba users/roles, một education level, ba grades, ba subjects và hai tập quan hệ.
Không đọc toàn bộ JSON array vào RAM: ijson parse từng record, heap giữ tối đa batch_size aggregate;
cache lookup tối đa 256 đối tượng. Bộ nhớ quan hệ giới hạn bởi một resource aggregate.
Nguồn fixture phải bất biến suốt một lần sync. Cache reset khi bắt đầu stream.
Preflight validate toàn bộ ID/relations, kể cả row nằm trước incremental cursor, để không bỏ sót duplicate
giữa các batch. Disk-backed scratch index giữ RAM bounded; xem production_source_adapter.md.

Thay nguồn thật: implement port bằng adapter mới, kiểm tra DTO/watermark, đổi wiring trong `composition.py`.
Chọn qua SOURCE_ADAPTER; hiện chỉ json hoạt động, production fail-fast với BLOCKED_SOURCE_MAPPING.
Không sửa MasterSync. Chưa có URL, credential hoặc schema nguồn thật và không có adapter giả mạo dịch vụ thật.
