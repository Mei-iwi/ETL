# Metadata và aggregation ablation

Tên được chuẩn hóa: `title_content` là title + content; tên cũ `title_only` chỉ dùng để đọc artifact baseline,
không chỉ vector hóa title. `verified_full_context` là contextual đang có. Dùng cùng model/revision/corpus,
query và nhãn; mỗi input strategy tạo artifact riêng theo fingerprint, không sửa vector Mongo cũ.

Chọn strategy bằng28 validation: ưu tiên resource P@1, snippet P@1, MRR, tie ưu tiên full context hiện có.
Sau đó chọn policy bằng validation với resource P@1 không thấp hơn best-single; tối đa3 Chunk cho mean/evidence.
Raw/context agreement thử alpha0/0,25/0,5/0,75/1; không thêm mô hình hoặc từ đồng nghĩa.
Khóa lựa chọn trước khi tính holdout: subject/grade+content, mean Top3.

| Strategy | Validation resource P@1 | Holdout resource P@1 | Holdout Chunk P@1 | Holdout snippet P@1 | Holdout resource R@5 |
| --- | --- | --- | --- | --- | --- |
| raw_content | 0.9000 | 0.8333 | 0.6667 | 0.6667 | 0.9167 |
| title_content | 0.8500 | 0.9167 | 0.8333 | 0.8333 | 0.9167 |
| title_heading_content | 0.8000 | 0.9167 | 0.7500 | 0.7500 | 0.9167 |
| subject_grade_content | 0.9000 | 0.9167 | 0.7500 | 0.7500 | 0.9167 |
| verified_full_context | 0.8500 | 0.9167 | 0.6667 | 0.6667 | 0.9167 |

Holdout có12 câu có đáp án/8 no-answer; các hàng này chưa áp dụng abstention.
Mean Top3 validation P@1=1,0 nhưng holdout=0,8333, thấp hơn best-single cùng strategy=0,9167;
**không đưa mean vào runtime**. Title+content snippet có vẻ tốt hơn full context trên holdout,
nhưng không chọn lại strategy từ holdout đã quan sát. Không sửa qrels khi thấy các câu sai.

Title/heading có thể tăng similarity giữa nhiều đoạn cùng tài liệu, kể cả header/footer không chứa đáp án.
Subject/grade không tự động tốt hơn: kết quả phụ thuộc intent và nhiều loại metadata đang còn ít ví dụ.
Không khẳng định mọi khác biệt metric là do metadata, vì qrels chưa đầy đủ và nhãn chưa duyệt.
Đổi input strategy trong tương lai phải tạo generation mới, verify coverage và có rollback.
