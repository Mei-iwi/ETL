# Kiểm chứng và tối ưu BGE-M3 — vòng tiếp theo

**Tổng trạng thái: PARTIAL.** Hoàn tất10 checkpoint trong phạm vi có thể thực thi. Có một tối ưu runtime
giữ nguyên ranking; các thử nghiệm quality/no-answer chưa qua gate holdout nên giữ offline.
Qrels vẫn DRAFT và live authenticated semantic chưa có đủ điều kiện, không tuyên bố nghiệm thu tổng thể.

## A. BASELINE

Nhánh `feat/search/bge-m3-contextual-retrieval`, HEAD `971496c`; giữ dirty work từ trước.
BAAI/bge-m3 revision `5617a9f61b028005a4858fdac845db406aefb181`, dense1024/FP32.
Frozen corpus checksum `9f96ed218bae04555e3bae7b357ffdf3aa25b795ac6134fa4431ab37b75e87bf`. Audit cũ33 versions/32 resources/363 Chunk/292 bag
được tái sử dụng; không lặp audit DB hoặc benchmark đã xong. Toàn bộ JSON/anchor/checksum qrels đã đối chiếu.
Baseline contextual trên25 câu có đáp án: P@1=0,92; Chunk P@1=0,80; R@5=1; MRR=0,9533; nDCG=0,9691;
5/5 câu no-answer có kết quả ở threshold0. Không so trực tiếp metric đó với holdout khác câu hỏi.
Cấu hình local kiểm tra lại vẫn semantic=false, rerank=false, threshold0. Không coi file env là bằng chứng
model resident của API. Dữ liệu DB hiện tại không được tái audit trong vòng này.

## B. ROOT CAUSE

Keyword/body/context khác nhau; metadata có thể làm header/đoạn cùng chủ đề thắng evidence.
Quan hệ phủ định và common-denominator vs GCD/LCM vẫn khó. Full report:
[QUALITY.md](QUALITY.md). Đính chính bằng evidence gốc: ColBERT B2 sai vì đoạn vòng tuần hoàn nước,
không phải OCR như phần diễn giải trước đó. Không sửa evidence baseline để che khác biệt này.

## C. QUALITY OPTIMIZATION

Thêm12 validation và20 holdout có snapshot/checksum. Strategy/policy được khóa bằng validation trước
khi tính holdout. Best-single, bounded mean Top2/3 và raw/context agreement đều được so sánh.
Mean Top3 thắng validation nhưng resource P@1 holdout giảm91,67%→83,33% so best-single cùng strategy:
**không áp dụng**. Năm ablations, metric resource/chunk/snippet: [ABLATION.md](ABLATION.md).

## D. NO-ANSWER RESULTS

Threshold tuyệt đối validation chọn≈0,495 giảm FPR holdout100%→12,5% nhưng chặn nhầm25% câu có đáp án;
R@5 còn75%. Margin có cải thiện hạn chế và phụ thuộc duplicate/corpus/scope. Chưa có rule đủ an toàn
để bật mặc định. Không sửa `total` cho đẹp, không gán cosine thành xác suất.
[NO_ANSWER.md](NO_ANSWER.md) có trade-off, bucket unjudged riêng và đề xuất API ba trạng thái chưa triển khai.

## E. PERFORMANCE PROFILE

Model thật Docker4GiB/2CPU: load≈8.46s; thread2 warm encode P95≈489ms;
sampled peak RSS≈2.14GiB. Batch1/2/4/8 được đo, không thấy cơ sở tăng mặc định.
Không gọi ranking offline dưới1ms là HTTP latency. [PROFILE.md](PROFILE.md) có CPU, tokens, throughput và giới hạn.

## F. OPTIMIZATION RESULTS

Chỉ giữ tối ưu reuse normalization trong `SemanticService`: validation vector + cosine P95
235.95→82.51ms trên cùng30 query×363 vector,
thứ tự Chunk không đổi, delta score≤2.22e-16. Bảo toàn kiểm tra schema/model/generation và quyền.
Không thay input strategy, threshold, RRF, query cache hoặc thứ tự build; không live reindex.

## G. MULTILINGUAL RESULTS

Holdout mới có9 vi/9 en/2 mixed (cả no-answer); chỉ12 câu có đáp án. RESULTS ghi kết quả selection riêng
vi/en/mixed và category. Mixed chỉ2 câu, không đủ kết luận tổng quát. Không dịch câu hỏi hoặc hardcode synonym.
Unicode NFC/NFD cosine1; quá dài bị từ chối, không truncate.

Full-context trên các câu holdout có đáp án:

| Ngôn ngữ | Số câu | Resource P@1 | Snippet P@1 |
| --- | --- | --- | --- |
| Việt | 5 | 80% | 20% |
| Anh | 5 | 100% | 100% |
| Mixed | 2 | 100% | 100% |

Snippet tiếng Việt còn yếu ở tập câu mới; không suy rộng kết quả25 câu cũ thành độ chính xác cho mọi query.
Qrels theo anchor có thể thiếu đoạn dẫn chứng khác cũng hợp lệ; cần review trước khi quy mọi sai khác thành lỗi model.

## H. COLBERT RESULTS

Đã tái lập đúng passage gây lỗi B2: raw MaxSim water-cycle0,596167 > Word0,538561 > OCR0,465714.
Giữ query, thay passage bằng verified context ở case đã biết: Word0,551649 > water-cycle0,494848 > OCR0,437877.
Token interaction/độ dài trong RESULTS. Đây là một case study validation, chưa chứng minh cải thiện holdout;
không chuyển runtime sang ColBERT contextual, không penalty theo file type. ColBERT vẫn OFF.

## I. COMPATIBILITY

BoW/TF-IDF/RRF/extractor/Auth/Frontend contracts/SOURCE/MASTER/storage key/hash giữ nguyên.
Hai correctness fix trước đó được giữ; normalize không mutate store. Không query cache dùng chung user,
không scan thêm generation hoặc read content cấm để tính margin. Chưa sửa API ba trạng thái; mới có đề xuất.
Trên live: /health200; model-status403 AUTH_PROXY_UNTRUSTED; không bypass.

## J. VERIFICATION

Docker hồi quy **324 PASS**,0 FAIL/0 SKIP; selection sau guard cuối **15 PASS**.
Frontend46 PASS, Edge fixture1 PASS, TypeScript/Vite build PASS, Ruff và diff check PASS.
Model thật/offline ablation/profile đều đã chạy. [VALIDATION.md](VALIDATION.md) ghi lệnh và các lần lỗi đã xử lý.
Các nhóm regression guard/stale/model/index/version/resume nằm trong semantic/search/index suites;
cache/admission/ownership/concurrency/pagination nằm trong HTTP/runtime/access suites; no-answer/review có test mới.
Không dùng mock để công bố quality hoặc inference time.

## K. REMAINING LIMITATIONS

-11.616 dòng evidence private còn DRAFT,0 APPROVED; không độc lập với người xây bộ test. Reviewer workflow đã có.
- Holdout mới vẫn cùng corpus32 học liệu mẫu, thiếu exhaustive judgments và có nội dung trùng; cần corpus/người duyệt độc lập.
- Các scope nhỏ/user-specific hoặc corpus mở rộng chưa có calibration; không chọn ngưỡng mới sau khi xem holdout này.
- Chưa có session/proxy hợp lệ hoặc semantic schema/index provisioned ở môi trường thử nghiệm: live Search Vector/Hybrid,
  context, permissions, DB query-count/bytes và HTTP P50/P95 còn BLOCKED/NOT_RUN. Integration Mongo vật lý chưa có DB test được chỉ định.
- Docker timings phụ thuộc quota/host; peak RSS lấy mẫu, P99 chưa đủ mẫu, batch chỉ12 Chunk phân tầng.

## L. FINAL STATUS

| Checkpoint | Trạng thái |
| --- | --- |
|1 Baseline audit/checksum | PASS, tái sử dụng evidence; độ tin cậy nhãn PARTIAL |
|2 Root cause/evidence | PASS, có đính chính ColBERT |
|3 No-answer evaluation | PASS thực nghiệm; PARTIAL tính sẵn sàng runtime |
|4 Candidate/metadata ablation | PASS thực nghiệm; không áp dụng phương án chưa tổng quát |
|5 Performance profiling | PARTIAL: model/cosine thực, thiếu live DB/HTTP |
|6 Performance change | PASS reuse normalization, parity và hồi quy |
|7 ColBERT study | PASS case study; giữ OFF, thiếu quality diện rộng |
|8 Full regression | PASS các suite đã chạy |
|9 Live authenticated verification | BLOCKED; không cấp giả quyền hoặc tự provision DB |
|10 Report/handoff | PASS; tổng thể PARTIAL |

Files: [CHANGES.md](CHANGES.md), [RESULTS.json](RESULTS.json), [selection-lock.json](selection-lock.json),
[freeze.json](freeze.json), [Git status](git-status.txt).
Không commit/push/merge/deploy, không thay dữ liệu/env/schema/model hoặc chạy migration/reindex.
