# Profile và tối ưu hiệu năng

Model thật trong Docker QA Python3.12, `--network none --memory 4g --cpus 2`, model/source read-only,
không mount secret/DB. Checkpoint giữ nguyên. Một resident model được quan sát xuyên suốt; không cache query.
Model cold-load 8.461s. Encode được đo sau probe Unicode; không gọi nó là cold HTTP.
Sampled peak RSS 2.143GiB, interval20ms, có thể bỏ lỡ đỉnh ngắn hơn.
NFC/NFD sau normalization cho cùng câu, cosine1,0. Query quá dài trả SEMANTIC_TOKEN_LIMIT,
không truncate. Query có13–19 tokens trong mẫu validation; chưa đại diện query dài thực tế.

12 query validation×3 vòng mỗi cấu hình; warm loại vòng0. Không đủ mẫu để kết luận P99, nên P99=null.

| Torch threads | Mẫu | Encode P50 ms | Encode P95 ms | Warm P95 ms | CPU giây |
| --- | --- | --- | --- | --- | --- |
| 1 | 36 | 504.57 | 697.17 | 596.03 | 18.58 |
| 2 | 36 | 385.77 | 524.02 | 489.00 | 28.16 |
| 4 | 36 | 603.90 | 803.41 | 800.64 | 45.55 |

Thread2 tốt hơn1/4 trên phép đo container2CPU; thread4 có oversubscription.
Không thay torch global threading hoặc package/version của runtime đang chạy.

## Bottleneck cosine có sửa code

Trước: `_validated` chuẩn hóa để kiểm tra rồi bỏ kết quả; `cosine` lại validate/scale/norm mỗi query và vector.
Sau: giữ vector đã chuẩn hóa trong bản sao, chuẩn hóa query một lần mỗi tập phiên bản, dot product bằng fsum.
Không bỏ kiểm tra finite/dimension/L2/mapping/manifest, không mutate DB hoặc thay generic cosine của BoW.

Đo lại riêng khi không còn workload model/QA trong phiên:30 query thật×363 vector thật:

| Đường xử lý | P50 ms | P95 ms |
| --- | --- | --- |
| Trước | 211.12 | 235.95 |
| Sau | 69.80 | 82.51 |

Giảm P95 khoảng65.0% ở **validation vector + similarity**,
không phải toàn bộ API. Thứ tự toàn bộ Chunk giữ nguyên; sai khác score tối đa2.22e-16.
Lần đo thăm dò chạy cùng workload khác có P95 cao hơn; báo cáo dùng lần đo lại để tránh lẫn điều kiện.
Full service còn chi phí plan/hash, đọc PG/Mongo, policy và hydrate/snippet chưa đo live.

## Ingestion

363 input full-context:43–292 tokens, P50=76, P95≈143,7;0 vượt budget1024.
Vì vậy thời gian357s trước không do truncate hoặc mọi Chunk đều dài1024 tokens.
Thời gian đó là inference offline, model load đo riêng; không quy đổi thành tốc độ worker có DB.
Có216 input text khác nhau/363,147 lần lặp giữa các phiên bản;0 lần lặp trong cùng phiên bản.
Cache xuyên phiên bản có thể giảm encode, nhưng chưa thêm global cache vì chưa có thiết kế scope,
giới hạn/eviction/generation và bằng chứng hit rate vận hành. Resume theo record/model/input identity hiện có giữ nguyên.

12 Chunk phân tầng theo độ dài, đo batch1/2/4/8 với thứ tự length ascending/descending:

| Batch | Thứ tự | Giây/12 Chunk | Chunk/giây | Min cosine với batch1 |
| --- | --- | --- | --- | --- |
| 1 | length_ascending | 14.78 | 0.812 | 1.000000000000 |
| 1 | length_descending | 14.77 | 0.812 | 1.000000000000 |
| 2 | length_ascending | 15.36 | 0.781 | 1.000000000000 |
| 2 | length_descending | 15.73 | 0.763 | 1.000000000000 |
| 4 | length_ascending | 20.20 | 0.594 | 1.000000000000 |
| 4 | length_descending | 18.93 | 0.634 | 1.000000000000 |
| 8 | length_ascending | 17.61 | 0.681 | 1.000000000000 |
| 8 | length_descending | 27.47 | 0.437 | 1.000000000000 |

Nhãn thứ tự đã được sửa để phản ánh đúng sample ban đầu tăng dần và sample đảo giảm dần;
không đổi số đo. Hai thứ tự đều đã có phân tầng độ dài, không phải so sánh corpus nguyên bản với bucketing.
Batch lớn chưa có lợi ổn định; không tăng mặc định, không đổi thứ tự ingestion runtime.
Vector tương đương số học (khác component tối đa≈1,1e-7), không tuyên bố bitwise giống hệt giữa mọi batch.
Chưa chạy lại đầy đủ363 Chunk cho từng batch; không suy rộng throughput12 mẫu thành SLA.

## Stage chưa đo live

Auth/permission, Mongo round trips/bytes read, scope filtering, hydrate/snippet, HTTP serialization và end-to-end:
**NOT_RUN/BLOCKED** vì chưa có session và semantic index provisioned. Không thêm ANN cho363 Chunk.
Không giảm verification/projection để lấy tốc độ khi chưa có query trace thật.
Serialization JSON report offline≈0.565ms, không phải serialization API.
