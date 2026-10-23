# Đánh giá chất lượng Vector BGE-M3 — 23/10/2026

**Trạng thái tổng: PARTIAL.** Đã thực hiện audit READ ONLY local, benchmark offline với model thật,
sửa hai lỗi có kiểm thử tái hiện và chạy hồi quy. Chưa nghiệm thu chất lượng production:
qrels do agent đối chiếu nội dung, chưa được chuyên gia môn học duyệt; chưa đo Search API bằng phiên đăng nhập thật.
Không thay đổi cờ runtime, threshold, schema hoặc dữ liệu; không chạy backfill/reindex trên DB.

## A. CURRENT IMPLEMENTATION

Nhánh `feat/search/bge-m3-contextual-retrieval`; HEAD `971496c`. Đã có nhiều thay đổi chưa commit
từ nhiệm vụ triển khai Chunk Semantic trước đó; giữ nguyên toàn bộ. Xem [Git status](git-status.txt).

- Cấu hình local đang dùng `BAAI/bge-m3`, revision `5617a9f61b028005a4858fdac845db406aefb181`, 1024 chiều.
  Model local `Models/bge-m3`, không tải mạng. `SEMANTIC_ENABLED=false`: đường chạy được cấu hình hiện tại là legacy.
  Không suy diễn trạng thái process từ file `.env`; API model-status live bị chặn bởi ranh giới proxy.
- Legacy: `LocalSentenceEmbedding` + `IndexingService` embed `' | '.join(keywords)` của ContentBag;
  query là câu đã `normalize_query`, không phải chuỗi keywords. `SearchService._query_vector` dùng SentenceTransformer.
  Snippet legacy chọn bằng `binary_score`, có thể đúng học liệu nhưng sai đoạn dẫn chứng.
- Nhánh opt-in: `BgeM3Embedding`/FlagEmbedding 1.3.5, FP32 CPU, L2, max1024 tokens, batch2;
  raw/contextual Chunk; overflow bị từ chối, không âm thầm cắt. Context `verified-headings-v1` lấy title,
  môn/lớp và heading thực đã đồng bộ, không thêm metadata giả hoặc ID kỹ thuật vào embedding.
- `SemanticIndexService.scores` exact cosine trên Chunk đã được cấp quyền; lấy Chunk tốt nhất mỗi ContentBag,
  rồi `SearchService` gộp theo resource ID và kiểm tra lại quyền/snapshot. Hybrid giữ RRF hiện có.
  Chưa có ANN/vector index MongoDB Atlas. Giới hạn candidate và deadline vẫn áp dụng.
- MASTER local có32 học liệu,33 phiên bản,410 ContentUnit;33 INDEX_BUILD COMPLETED.
  Mongo có5 collection,293 ContentBag current cùng revision/1024D;33/33 snapshot được kiểm chứng.
  Corpus lấy phiên bản hợp lệ mới nhất:32 resources,363 leaf Chunk,292 bags;0 trùng chunk ID.
  Một bag còn lại thuộc phiên bản lịch sử hợp lệ, không phải bằng chứng dữ liệu rác. Không xóa.
- Chưa có hai collection semantic mới trong Mongo local; benchmark tạo vector **offline**, không cài schema.
  Norm292 vector legacy: min0.999999867, max1.000000107. Không thấy vector384 trộn trong corpus này.
- Query encoder legacy và FlagEmbedding được đối chiếu offline trên đủ30 câu cùng checkpoint:
  cosine nhỏ nhất0.9999999404; sai khác component tối đa1.1921e-7, ST3.4.1 trong venv cô lập.
  Đây không xác nhận package/model resident của process API đang chạy.

Audit: [audit.json](audit.json), [encoder parity](query-encoder-parity.json).
Corpus checksum: `9f96ed218bae04555e3bae7b357ffdf3aa25b795ac6134fa4431ab37b75e87bf`.
Không kết nối SOURCE Neon. Raw corpus/vector nằm dưới `.test-tmp/semantic-quality`, không đưa vào Git.

## B. INITIAL TEST RESULTS

Các dòng dưới tái lập offline Vector legacy trên corpus đủ32 học liệu, không áp dụng bộ lọc quyền của một user cụ thể.
Ảnh của người dùng không chứa đủ session/request để khẳng định thứ hạng live giống hệt.
Grade3=trả lời trực tiếp,2=đủ liên quan để trả lời,1=chỉ cùng chủ đề/thiếu giải thích,0=không đáp ứng.
Ground truth được gắn bằng dẫn chứng thân bài trước khi xem thứ hạng, không lấy title hoặc kết quả model làm nhãn.
Hai resource ID khác nhau cùng nội dung đều được đánh giá, không gộp vì giống tên.

| Query | Expected resources | Top1 legacy | Hạng đáp án đầu | Score Top1 | Phân tích |
| --- | --- | --- | --- | --- | --- |
| A1 — Cách cộng hai phân số khác mẫu số | HL26_R013, HL26_XDRV_R001 | HL26_R013 | 1 | 0.622286 | Thân bài có quy đồng rồi cộng tử; đúng chủ đề. |
| A2 — Tìm mẫu số chung rồi cộng các tử số | HL26_R013, HL26_XDRV_R001 | HL26_XDRV_R001 | 1 | 0.654586 | Diễn đạt lại vẫn tìm đúng bài phân số. |
| B4 — Trao đổi chất khí ở cây | HL26_R022, HL26_XDRV_R010 | HL26_R022 | 1 | 0.605995 | Word giải thích khí khổng/trao đổi khí; OCR chỉ có nhãn sơ đồ. |
| B3 — Tree hấp thụ khí như thế nào? | HL26_R022, HL26_XDRV_R010 | HL26_R022 | 1 | 0.567958 | Đúng Word; không suy ra tài liệu giải thích hô hấp tế bào. |
| B2 — How do plants make food using sunlight? | HL26_R022, HL26_XDRV_R010 | HL26_GDRV_R007 | 2 | 0.479778 | OCR chỉ grade1; Word giải thích ánh sáng, CO2, nước và chất hữu cơ đầy đủ hơn. |
| B1 — Quang hợp ở thực vật | HL26_R022, HL26_XDRV_R010, HL26_GDRV_R007 | HL26_R022 | 1 | 0.563855 | Câu ngắn chỉ yêu cầu chủ đề, sơ đồ OCR cũng có thể liên quan. |


32 kết quả là32 resource sau grouping, không phải32 Chunk. `total`, `ready_bag_count`, số ứng viên và `page/size`
có ý nghĩa khác nhau. Threshold mặc định0 giữ hầu hết cosine dương, nên tài liệu kém liên quan vẫn xuất hiện.
Không phát hiện lỗi sort/dedup định danh trong corpus này; lịch sử phiên bản phải được kiểm chứng thay vì xóa theo tên.

## C. METRICS BEFORE IMPROVEMENT

30 queries:25 có đáp án,5 không có đáp án;18 tiếng Việt,9 tiếng Anh,3 trộn ngôn ngữ.
16 validation/14 test. Test có11 câu có đáp án và3 câu không có đáp án. Các intent GCD/salt/Excel/literature
được giữ ở test; paraphrase của cùng intent không chia ngẫu nhiên hai bên. Hard-negative hô hấp kiểm tra
chủ đề gần quang hợp. Đây là bộ nhỏ, nhãn dự thảo; không phải benchmark độc lập hay exhaustively judged corpus.

Macro quality chỉ tính25 câu có đáp án;5 câu không có đáp án báo false-positive riêng.
Binary answer lấy grade>=2; nDCG giữ graded relevance0..3. P@5 chia cố định5 nên với hai đáp án đúng,
P@5 tối đa0.4 dù Recall@5=1. Không đổi mẫu số để làm đẹp số liệu.

Legacy trên25 câu: P@1=0.6800; P@5=0.4000; R@5=0.9600; MRR@10=0.8047; nDCG@10=0.8720;
Hit@1=0.6800; Hit@5=1.0000. Cả5 câu không có đáp án vẫn có kết quả (FPR1.0).
Baseline hồi quy trước sửa code: **204 PASS**. Hai test tái hiện mới **2 FAIL** trước bản sửa.

## D. ISSUES IDENTIFIED

1. Legacy vector từ keyword string mất ngữ cảnh thân bài, đặc biệt câu tiếng Anh; snippet lexical có thể sai.
2. Threshold0 trả nhiều kết quả; score answer và partial/unjudged chồng lấn, không có một hằng số an toàn cho mọi intent.
3. Contextual không luôn tốt hơn raw: C4 vẫn ưu tiên nội dung bảo mật tổng quát; D2 nhầm GCD/LCM với quy đồng.
   Chunk đầu bảng của C3, D1, F3 vẫn có thể thiếu dẫn chứng chuẩn dù học liệu đúng.
4. ColBERT trước sửa lấy TopK ContentBag toàn cục; một phiên bản có nhiều Bag chiếm phần lớn ngân sách.
5. Phiên bản không có root/unit được phép đọc vẫn bị kiểm tra manifest semantic; manifest thiếu gây503 cho cả truy vấn.
6. Word có nhiều đoạn rất ngắn/title/footer; OCR sơ đồ chỉ có từ khóa. Không thể đánh đồng topic với câu trả lời đầy đủ.
   Không sửa dữ liệu/OCR/chunking/BoW trong nhiệm vụ này.

## E. IMPROVEMENTS MADE

- Thêm `application/vector_quality.py`, tái sử dụng metric của `domain.evaluation`, tách resource/chunk/no-answer;
  compile qrels bằng anchor dẫn chứng + ResourceVersion/Chunk ID; calibration chỉ nhận validation, từ chối test.
- Thêm exporter local READ ONLY với SQL transaction read-only, timeout, giới hạn100 resources/2000 chunks,
  whitelist loopback, snapshot marker; không dùng Container để tránh kết nối SOURCE ngoài ý muốn.
- Thêm runner offline có checksum, kiểm tra checkpoint/dimension, NPZ hữu hạn/normalized, cache artifact riêng
  theo corpus+model+text+strategy; không dùng kết quả query cũ của ứng dụng hoặc ghi vào Mongo.
- Sửa `SemanticIndexService.rerank`: chọn Bag tốt nhất của mỗi phiên bản **trước** ngân sách rerank,
  tie ổn định. Với metadata hiện tại một phiên bản hợp lệ/resource, ngân sách phục vụ nhiều học liệu hơn.
  Không trộn score dense và ColBERT, không đổi RRF. Candidate policy có helper chung giữa service và evaluator.
- Sửa `scores`: trả rỗng sớm khi không có root/unit được phép; giữ fail-closed cho phiên bản được phép nhưng index thiếu/stale.
- Thêm10 test evaluation và2 test hồi quy lỗi. Sửa selector test E2E nút quay lại theo SVG+text hiện tại;
  không thay đổi UI sản phẩm/Auth để test qua.
- So sánh raw, contextual đầy đủ và title+content (tên `title_only`, **không phải title đơn lẻ**).
  Title+content chỉ là ablation offline, chưa thêm cờ runtime hay đổi format index.

## F. METRICS AFTER IMPROVEMENT

Tất cả25 câu có đáp án, threshold0; cùng model/checkpoint, corpus và nhãn:

| Phương án | P@1 | P@5 | R@5 | MRR@10 | nDCG@10 | Hit@1 | Hit@5 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| legacy | 0.6800 | 0.4000 | 0.9600 | 0.8047 | 0.8720 | 0.6800 | 1.0000 |
| raw | 0.9200 | 0.4000 | 0.9600 | 0.9467 | 0.9541 | 0.9200 | 0.9600 |
| contextual | 0.9200 | 0.4160 | 1.0000 | 0.9533 | 0.9691 | 0.9200 | 1.0000 |
| title_only | 0.9600 | 0.4160 | 1.0000 | 0.9733 | 0.9816 | 0.9600 | 1.0000 |


Test giữ riêng (11 câu có đáp án):

| Phương án | P@1 | P@5 | R@5 | MRR@10 | nDCG@10 | Hit@1 | Hit@5 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| legacy | 0.6364 | 0.4000 | 1.0000 | 0.7955 | 0.8710 | 0.6364 | 1.0000 |
| raw | 1.0000 | 0.4000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| contextual | 1.0000 | 0.4000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| title_only | 1.0000 | 0.4000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |


Test đạt1.0 trên một số metric do corpus nhỏ và có cặp nội dung tương tự ở hai resource ID,
không chứng minh tổng quát hóa production. Contextual tăng P@1 toàn bộ từ68% lên92%, title+content96%;
không chọn title+content làm mặc định chỉ từ kết quả trên test. Bộ test này đã được quan sát, vòng tuning sau cần test mới.

Đánh giá Chunk tách riêng; legacy là **đoạn snippet lexical được hiển thị**, không giả định là dense Chunk retrieval:

| Phương án | P@1 đoạn | R@5 đoạn | MRR@10 đoạn | nDCG@10 đoạn | Hit@5 đoạn |
| --- | --- | --- | --- | --- | --- |
| legacy | 0.3600 | 0.4533 | 0.4242 | 0.4367 | 0.5200 |
| raw | 0.6000 | 0.7500 | 0.6920 | 0.7335 | 0.8400 |
| contextual | 0.8000 | 0.8833 | 0.8447 | 0.8461 | 0.9600 |
| title_only | 0.8400 | 0.8833 | 0.8907 | 0.8683 | 0.9600 |


Calibration chọn max macro-F1 trên16 validation (no-answer đúng khi abstain), tie theo recall rồi threshold thấp hơn.
Chỉ là thí nghiệm, **không áp dụng vào `.env` hoặc runtime**:

| Phương án | Threshold thử nghiệm | Test R@5 | Test no-answer FPR |
| --- | --- | --- | --- |
| legacy | 0.528264 | 0.8182 | 0.0000 |
| raw | 0.542967 | 0.9091 | 0.3333 |
| contextual | 0.525904 | 0.9091 | 0.6667 |
| title_only | 0.540505 | 0.9091 | 0.6667 |


Ngưỡng contextual làm R@5 test giảm1.0→0.9091 nhưng vẫn trả kết quả cho2/3 câu không có đáp án.
Không đủ căn cứ bật ngưỡng này. Score distribution trong results.json ghi `unjudged_or_negative`,
không gọi tất cả tài liệu chưa gán nhãn là negative chắc chắn.

ColBERT thật (MaxSim head local), hai query B2/E1, TopK10/20/50:

| Query | K | Policy | Ứng viên | Phiên bản riêng | P@1 dense trước | P@1 ColBERT | nDCG@10 ColBERT |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B2 | 10 | bag_first | 10 | 6 | 1.0 | 0.0 | 0.7005 |
| B2 | 10 | version_diverse | 10 | 10 | 1.0 | 0.0 | 0.7005 |
| B2 | 20 | bag_first | 20 | 10 | 1.0 | 0.0 | 0.7005 |
| B2 | 20 | version_diverse | 20 | 20 | 1.0 | 0.0 | 0.7005 |
| B2 | 50 | bag_first | 50 | 21 | 1.0 | 0.0 | 0.7005 |
| B2 | 50 | version_diverse | 32 | 32 | 1.0 | 0.0 | 0.7005 |
| E1 | 10 | bag_first | 10 | 2 | 1.0 | 1.0 | 1.0000 |
| E1 | 10 | version_diverse | 10 | 10 | 1.0 | 1.0 | 1.0000 |
| E1 | 20 | bag_first | 20 | 8 | 1.0 | 1.0 | 1.0000 |
| E1 | 20 | version_diverse | 20 | 20 | 1.0 | 1.0 | 1.0000 |
| E1 | 50 | bag_first | 50 | 11 | 1.0 | 1.0 | 1.0000 |
| E1 | 50 | version_diverse | 32 | 32 | 1.0 | 1.0 | 1.0000 |


B2: ColBERT ưu tiên OCR sơ đồ grade1, làm P@1 giảm1→0; E1 giữ đúng. Policy đa dạng hóa khắc phục
crowding (E1 K10:2→10 phiên bản), nhưng chưa tăng quality trên hai query này. Giữ rerank OFF mặc định.
K50 version-diverse có32 ứng viên vì chỉ có32 phiên bản hợp lệ. Không tạo ứng viên giả cho đủ50.

Hiệu năng offline (không phải độ trễ API):

| Phương án | Ranking P50 ms | Ranking P95 ms | Encode363 Chunk lần đầu giây | Artifact NPZ bytes |
| --- | --- | --- | --- | --- |
| legacy | 9.393 | 15.835 | N/A, dùng bag cũ | None |
| raw | 1.260 | 1.667 | 196.502 | 1382345 |
| contextual | 0.675 | 1.054 | 357.082 | 1382297 |
| title_only | 0.641 | 0.763 | 261.258 | 1382337 |


Model load lần cuối 10.474s; encode30 query lần đầu 6.244s.
Vòng cuối tái sử dụng NPZ cùng checksum/identity, không ghi encode cache-hit thành thời gian inference.
ColBERT union61 candidates B2:56.306s;
union71 candidates E1:63.193s,
CPU threads2, mỗi batch rerank tối đa50.
Đây là chi phí union dùng chung, **không phải** P50/P95 riêng cho K10/20/50.
RSS lấy mẫu sau ranking khoảng0.700–0.705GiB,
không phải peak memory (Windows có thể paging).
Raw float32 tối thiểu363×1024×4=1,486,848bytes/generation; chưa tính metadata, index và lịch sử.
Collection legacy thực tế logical4,038,423bytes, storage5,517,312bytes, indexes143,360bytes.
Serialization report offline 12.649ms (~1,227,604bytes);
DB retrieval/HTTP serialization/end-to-end live **NOT_RUN**. Không dùng thời gian sort dưới1ms để hứa API dưới1ms.

## G. MULTILINGUAL & CONTEXTUAL RESULTS

Chỉ tính câu có đáp án ở từng ngôn ngữ:

| Ngôn ngữ | Số câu có đáp án | Legacy P@1 | Raw P@1 | Contextual P@1 | Title+content P@1 |
| --- | --- | --- | --- | --- | --- |
| vi | 15 | 0.8000 | 1.0000 | 0.9333 | 1.0000 |
| en | 7 | 0.2857 | 0.7143 | 0.8571 | 0.8571 |
| mixed | 3 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |


B2 tiếng Anh: legacy OCR rank1/Word rank2 → contextual Word rank1 (score0.569831).
Raw lại ưu tiên sơ đồ vòng tuần hoàn nước (score0.592980), chứng minh không thể mặc định raw luôn tốt hơn.
B3/B4 tìm đúng Word mô tả khí khổng và khí CO2/O2; câu N4/N5 về hô hấp tế bào không có đoạn giải thích
đủ trong corpus, tài liệu quang hợp chỉ grade1. Từ khóa “thực vật/khí” không biến hai quá trình thành đồng nghĩa.
Title+content giúp C4 chọn đúng tài liệu nhưng D2 vẫn ưu tiên GCD/LCM; cần nhãn/intent đa dạng hơn trước điều chỉnh metadata.

## H. COMPATIBILITY STATUS

| Hạng mục | Kết quả thực hiện |
| --- | --- |
| Baseline hồi quy trước sửa | PASS204; XML lưu kèm |
| Hai lỗi tái hiện trước sửa | FAIL2 có chủ đích; sau sửa PASS trong suite216 |
| Docker Python3.12, source/tests hiện tại mount read-only, network none | PASS216,2 warnings; không kết nối DB |
| Backend auth single-admin/MASTER/Auth-DB fixture | PASS87; không dùng DB Dev/Production |
| Frontend API/runtime/model-status Node tests | PASS46 |
| Frontend TypeScript + Vite build hiện tại | PASS |
| Edge E2E search với HTTP fixtures | PASS1 trên build mới; trước đó sửa selector cũ |
| Ruff các file evaluation/semantic/tests được sửa | PASS (`--no-cache`) |
| Live GET /health | PASS HTTP200 |
| Live GET /api/v1/search/model-status trực tiếp | HTTP403 AUTH_PROXY_UNTRUSTED, giữ fail-closed |
| Search live có phiên đăng nhập thật, pagination/context và latency theo user | BLOCKED bởi thiếu session/proxy được cấp; không giả quyền |
| SOURCE key/hash, pipeline OCR/extractor/ContentBag, BoW và Hybrid RRF | Không sửa; hồi quy Search/index/access/evaluation PASS |
| Mongo semantic schema/backfill/live reindex | NOT_RUN, chưa được kích hoạt; không thay đổi5 collection hiện có |

Lệnh và evidence: [VALIDATION.md](VALIDATION.md). Baseline legacy được replay trong môi trường model cô lập;
không thay package của Backend đang chạy. Không thay security để đạt kết quả benchmark.

## I. REMAINING LIMITATIONS

1. Qrels `AGENT_REVIEWED_DRAFT_REQUIRES_DOMAIN_REVIEW`,32 học liệu mẫu,30 query; chưa có người thứ hai duyệt
   và chưa gán nhãn hết pool. Điểm số là sơ bộ, chưa đủ nghiệm thu hoặc chọn ngưỡng production.
2. Có cải thiện offline từ pipeline Chunk đã triển khai, **chưa triển khai kết quả lên live index**.
   Hai bản sửa correctness được kiểm chứng; không gán toàn bộ cải thiện92% cho riêng hai bản sửa này.
3. Chưa đo live authenticated API, HTTP P50/P95, worker memory peak, nhiều user đồng thời hoặc corpus lớn.
   Regression fixture/Edge không thay thế nghiệm thu truy vấn thật qua reverse proxy.
4. Dense score không phân tách hoàn toàn no-answer/hard negative. ColBERT đắt và có hồi quy quality; không bật mặc định.
5. Context chỉ có thể dùng metadata thật; không tự bổ sung kiến thức sinh học/đoạn thiếu. Thay title/metadata phải
   sinh generation mới; không sửa vector cũ tại chỗ. Kế hoạch reindex giữ lịch sử và rollback của nhánh opt-in vẫn áp dụng.
6. Một lần chạy PowerShell chuyển warning/progress stderr thành NativeCommandError/exit1 dù JSON hoàn tất.
   Đã chạy lại đầy đủ bằng Python subprocess: exit0, đủ120 rows/12 rerank; status chất lượng vẫn PARTIAL do nhãn DRAFT.

## J. FINAL STATUS

**PARTIAL**: hoàn tất phần audit, tooling, experiments và hai bản sửa trong phạm vi an toàn.
**PASS** cho các suite đã chạy nêu trên. **BLOCKED/NOT_RUN** cho nghiệm thu live auth/latency và triển khai chỉ mục.
Không commit, push, merge, deploy; không thay `.env`/threshold/schema/dữ liệu, không kết nối SOURCE.

Trước kích hoạt: chuyên gia duyệt qrels và mở rộng tập test độc lập; nghiệm thu qua session/proxy hợp lệ;
đo latency/memory tải thật; chỉ sau đó lựa chọn strategy/threshold và chạy kế hoạch index versioned có rollback.
Kết quả máy đọc được: [results.json](results.json); nhãn + dẫn chứng ID: [qrels.json](qrels.json);
thiết kế truy vấn/anchor: [query-specification.json](query-specification.json).
