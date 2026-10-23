# Root cause và tính hợp lệ thực nghiệm

Baseline giữ nguyên checksum, checkpoint và qrels; không sửa test cũ để tăng metric. Model query adapter parity
trước đó cosine≥0,99999994. File `freeze.json` cố định32 query mới/anchor/checksum trước khi tính ranking;
12 validation bổ sung các hard negative,20 holdout dùng intent mới hơn (giảm giá, vòng nước, thermometer,
tỉ lệ thuận, bảng khảo sát, lịch sử). Cùng corpus nhỏ và nhãn agent nên chưa là benchmark có giảng viên duyệt.
Holdout nay đã được xem: không tiếp tục tuning trên nó.

| Case cũ | Bằng chứng và phân loại |
| --- | --- |
| B2 quang hợp tiếng Anh | Đính chính diễn giải trước: JSON gốc cho thấy ColBERT top1 sai là đoạn vòng tuần hoàn nước, không phải OCR. Đoạn này nói Mặt Trời làm nước bay hơi và thực vật đưa hơi nước vào không khí. Word có lời giải thích quang hợp đầy đủ; OCR chỉ là partial. MaxSim ưu tiên khớp token không bảo đảm đúng quá trình/đầy đủ giải thích; không thêm penalty OCR. |
| C4 MFA có thay kiểm tra tên miền? | Có câu phủ định trực tiếp trong slide bảo mật. Contextual trước đó chọn email bảo mật tổng quát. Metadata/common-topic similarity lấn bằng chứng quan hệ phủ định; không phải corpus thiếu đáp án. |
| D2 common denominator | Bài phân số có quy đồng; UCLN/BCNN liên quan nhưng không giải thích đúng thao tác được hỏi. Ranking lẫn khái niệm gần nhau, không giải quyết bằng tăng threshold cho mọi query. |
| F3 lọc muối hòa tan | Corpus nói lọc cho chất không tan, cô cạn thu muối. Một đoạn chỉ nhắc hỗn hợp/từ khóa có score cao hơn đoạn giải thích. Cần nhiều evidence và giảng viên kiểm tra mức suy luận của qrels. |
| N4/N5 hô hấp | Bài quang hợp/khí khổng cùng chủ đề nhưng không có giải thích hô hấp tế bào. Corpus thiếu đáp án đầy đủ; partial không tính là answer. |
| Đúng resource, sai Chunk | C3/D1/F3 trong baseline; title/footer và các đoạn ngắn cùng context có thể điểm cao. Snippet phải dùng locator của Chunk quyết định, không chọn lại đoạn theo lexical score rồi gọi đó là bằng chứng dense. |

Tách metric: Resource-level dùng ID học liệu; Chunk-level xếp toàn bộ Chunk; snippet-level lấy một đoạn đại diện
mỗi resource theo đúng policy. Không gọi snippet-level là toàn bộ Chunk retrieval. Resource có nhiều Chunk không
được cộng điểm vô hạn. Precision@K dùng mẫu sốK, recall dùng các đáp án grade≥2, nDCG giữ grade0..3;
câu không có đáp án báo FPR riêng, không kéo recall bằng denominator giả.

Đã thử: best-single, mean Top2/Top3 có giới hạn, chọn evidence trong Top3 theo raw/contextual agreement.
Mean Top3 được validation chọn (resource P@1=100%, snippet P@1=90%) nhưng holdout resource P@1
chỉ83,33%, thấp hơn best-single của cùng strategy91,67%. **Không đưa mean hoặc agreement vào runtime.**
Đây là bằng chứng overfit trên validation nhỏ, không chọn lại policy bằng holdout.

Duplicate sensitivity trong RESULTS chỉ collapse các fingerprint **toàn bộ văn bản Chunk giống hệt** để phân tích,
không đổi grouping production. P@1/R@5 holdout không đổi trong view này. Nó không bắt được mọi bản sao có
khác cách phân đoạn, không chứng minh corpus hoàn toàn độc lập. Các resource ID khác nhau vẫn hợp lệ;
không gộp theo title và không xóa phiên bản lịch sử.

Qrels chưa đầy đủ: nhiều resource/chunk vẫn unjudged; một đoạn khác có thể cũng là dẫn chứng đúng.
Exporter private cho người duyệt đọc toàn bộ pool, kể cả câu no-answer; model score không được dùng làm nhãn.
Không tự đánh dấu dữ liệu đã nghiệm thu. Xem [quy trình review](NO_ANSWER.md#duyệt-nhãn-bởi-giảng-viên).
