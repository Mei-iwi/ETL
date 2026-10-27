# Bàn giao mở rộng media/song ngữ — 26/10/2026

## Tích hợp main — 27/10/2026

Giữ migration xóa mềm `0017` của main, chuyển migration media thành `0018` nối tiếp để Alembic chỉ có một head. Giữ cả hợp đồng dịch và các kiểm tra visibility/deletion của main; các endpoint export/retry/frame media cũng bị chặn khi học liệu đã xóa mềm. Không chạy migration hoặc sửa DB đang phục vụ trong lượt xử lý conflict. DB từng dùng `0017` media trước merge cần đối chiếu lịch sử/schema riêng trước khi nâng, xem README.

Kiểm tra trên DB cô lập: media/Drive/import/processing/xóa mềm/migration **283 pass, 1 skip**; đối soát/index/context/metadata **98 pass**; test nâng/hạ migration media sau xóa mềm **1 pass**; production frontend build exit 0. Bộ test frontend chọn lọc có **10 pass, 1 fail**: browser search UI timeout ở kiểm tra refresh sau focus; chạy riêng vẫn fail assertion tại `searchUi.test.mjs:573`, không phải build/type error. Log `.test-tmp/merge-main/`. Không thay luồng đồng bộ để xử lý conflict. Resolution được gộp vào 5 commit hiện có bằng rebase lên main, không tạo commit sửa conflict riêng.

## Cập nhật sau tải model và phục hồi đồng bộ

Large-v3 đã tải đủ weights **3.087.284.237 byte**, SHA-256 local đã kiểm lại và khớp `69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1`. Report evaluator thực tế `.test-tmp/asr-large-v3.json` ghi CPU/int8, beam=3, window=15, 4 threads, revision `edaa852ec7e145841d8ffdb056a99866b5f0a478`; chạy với `HF_HUB_OFFLINE=1`. Không chạy lại benchmark khi cấu hình/dữ liệu không đổi. Base/small đã được xóa theo yêu cầu sau lần đo small; các số cũ chỉ còn là bằng chứng lịch sử.

| Mẫu local | small WER / CER | large-v3 WER / CER | Giây small → large-v3 |
|---|---|---|---|
| Việt | 4,35% / 1,56% | 0% / 0% | 9,050 → 24,419 |
| Pháp | 10,53% / 5,49% | 0% / 0% | 7,093 → 25,926 |
| Nhật | — / 4,44% | — / 6,67% | 7,267 → 24,099 |

Large-v3 tốt hơn trên mẫu Việt/Pháp, kém hơn trên mẫu Nhật; không suy rộng từ một mẫu/ngôn ngữ, chưa kiểm chứng video 30 phút hoặc peak RAM.

Hai học liệu “Lập trình Python mp3”/“Lập trình python mp4” bị `SOURCE_VERSION_MUTATED` trước khi tạo phiên bản đích/job: hash trong hàng chờ MASTER cũ khác hash SOURCE hiện tại. Kiểm tra thêm thấy tham chiếu SOURCE dùng `lap_trinh_python.mp3/.mp4`, trong khi Drive dùng `LapTrinhPython.mp3/.mp4`. Tệp thật đã xác minh hash khớp SOURCE hiện tại, container hợp lệ, thời lượng tương ứng 223,974/223,933 giây. SOURCE là Neon qua tài khoản chỉ đọc; không sửa DB nguồn hoặc đổi tên tệp Drive.

Thêm cấu hình `SOURCE_DRIVE_FILENAME_ALIASES` mặc định `{}`: chỉ ánh xạ đúng `files/<id>/<tên nguồn>` sang tên Drive đã kiểm chứng, cùng phần mở rộng và không chứa đường dẫn; các tham chiếu khác vẫn khớp tên nghiêm ngặt. Vẫn kiểm tra MIME, root, revision, size, hash. Cấu hình local chỉ gồm hai alias đã xác minh. Phục hồi đúng hai hàng chờ chưa có phiên bản/job trong MASTER development bằng transaction có kiểm tra trạng thái/hash cũ; giữ kiểm tra bất biến cho phiên bản đã nhập. Bản ghi trước sửa và kết quả xác minh ở `.test-tmp/sync-fix/`, không đưa vào Git. API, source-file worker và OCR worker đã restart; model ASR được nạp trong tiến trình con theo job.

Regression cho Drive/import/reference/lineage: **146 pass, 1 skip**, 113,23 giây (`.test-tmp/sync-fix/unit.log`); Ruff pass. Có test alias ràng buộc ID/tên nguồn, không cho đổi loại tệp hoặc chèn đường dẫn, vẫn chặn MIME/root sai; test mutation của phiên bản đã nhập giữ nguyên.

Kiểm tra trước khi chia commit/push: nhóm media/runtime/processing/retry/search context/indexing/resource versions **145 pass**, 107,75 giây; frontend upload **6 pass**; TypeScript/Vite production build exit 0 (có cảnh báo chunk JS lớn hơn 500 kB). Log `.test-tmp/sync-fix/pre-push-unit.log`, `pre-push-frontend-test.log`, `pre-push-build-verified.log`. Không build Docker trong lượt này.

Sau phục hồi, cả MP3 và MP4 đã tạo được phiên bản đích và job xử lý, không còn lỗi nhập tệp. MP3 đã hoàn tất ASR: 2.803 ký tự, 30 đoạn speech, timestamp cuối 223,152 giây trên tệp 223,974 giây, warnings rỗng; metadata lưu đúng large-v3/revision/CPU/int8/4 threads. MP4 đang chạy ASR kế tiếp tại thời điểm bàn giao, chưa xác nhận hoàn tất. Worker giữ 1 slot và timeout media 900 giây. Đây là kiểm tra pipeline trên tệp thật khoảng 3 phút 44 giây, không có reference để tính WER/CER; chưa thay thế nghiệm thu video 30 phút. Trạng thái gần nhất lưu ở `.test-tmp/sync-fix/latest-status.json`.

## Lượt tải large-v3 ban đầu — lịch sử trước khi tải thành công

Đã đổi riêng `WHISPER_MODEL_PATH` trong `.env` sang `D:/HK7/KLTN/ETL/Models/faster-whisper-large-v3`, giữ CPU/int8/4 threads/1 slot. `.env.example` dùng đường dẫn mẫu có chú thích, Settings mặc định vẫn `None` khi chưa cài. Giữ small/base, Argos, giới hạn 50 MiB/1.800 giây, timeout media 900 giây, VAD/beam/ngưỡng lọc hiện tại; không đổi UI/DB/embedding. Chưa restart API/worker đang dùng.

Nguồn đã kiểm tra: [model card Systran](https://huggingface.co/Systran/faster-whisper-large-v3) xác nhận chuyển từ full multilingual `openai/whisper-large-v3` sang CTranslate2; [faster-whisper 1.2.1](https://github.com/SYSTRAN/faster-whisper/tree/v1.2.1) hỗ trợ model này. Hugging Face API trả revision **`edaa852ec7e145841d8ffdb056a99866b5f0a478`**. Allowlist thực tế: `config.json`, `model.bin`, `preprocessor_config.json`, `tokenizer.json`, `vocabulary.json`; tổng khoảng 3,09 GB, ổ đĩa còn 34.266.963.968 byte trước tải. Không tải PyTorch/dataset mới. Phiên bản cài: faster-whisper **1.2.1**, CTranslate2 **4.8.3**, huggingface-hub **1.33.0**; không nâng dependency.

**Tải chưa hoàn tất:** `snapshot_download` đã nhận config/preprocessor/tokenizer/vocabulary, nhưng Xet không tiến triển; thử HTTP (`HF_HUB_DISABLE_XET=1`) bị đứng ở **20.971.520 byte weights** hơn hai phút. Đã dừng riêng tiến trình tải, giữ cache. Chưa có `Models/faster-whisper-large-v3/model.bin`, chưa có checksum local của large-v3, **chưa chạy smoke/model thật**, chưa có số đo chất lượng/tốc độ large-v3. SHA-256 kỳ vọng từ metadata LFS nguồn là `69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1`; đây không phải kết quả checksum local. Artifact chẩn đoán: `.test-tmp/large-v3/source.json`, `download-status.json`, `download*.log`. Không cần quyền DB; không truy cập DB production trong lượt này.

Runtime bổ sung kiểm tra bộ tệp local trước khi gọi WhisperModel: tránh nhánh faster-whisper tự lấy tokenizer khi thiếu `tokenizer.json` dù `local_files_only=True`; model lỗi trả `STT_RUNTIME_UNAVAILABLE`, không fallback. Provider ghi model/device/compute_type, phiên bản CTranslate2, beam/window/threads và revision nếu có metadata tải của weights; revision `null` khi metadata không có. Script đánh giá thêm timeout hữu hạn tùy chọn và thời gian nạp model riêng với decode/VAD/inference; không đổi chuẩn hóa WER/CER. Small đã kiểm lại SHA-256 `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671`, revision lịch sử `536b0662742c02347bc0e980a01041f333bce120`; provenance riêng ở `.test-tmp/large-v3/small-provenance.json` vì cache model cũ không có metadata weights.

Kết quả lượt này (log `.test-tmp/large-v3/`):

| Kiểm tra | Kết quả | Phạm vi |
|---|---|---|
| Unit media: runtime mới, song ngữ và chính sách media | **40 pass**, 73,48 s | SQLite/memory fixtures cô lập; `unit.log` |
| Runtime chạy lại sau chỉnh sửa cuối | **11 pass**, 0,20 s | Model thiếu/hỏng, offline/no fallback, truyền tham số/ngôn ngữ, nguyên ngữ/timestamp, lọc speech/silence, offset audio trễ và bỏ OCR; `runtime-offset-final.log` |
| Integration FFmpeg/ffprobe/Tesseract/Argos thật, không ASR | **6 pass**, 4 deselected, 21,68 s | OCR video không audio, bỏ OCR khi audio hiện diện, phụ đề, silence, file sai/giới hạn, dịch; `integration-no-asr.log` |
| Ruff check/format phạm vi sửa | Pass | Runtime, evaluator, test runtime |
| Smoke large-v3, speech MP3/MP4, chuyển ngôn ngữ, audio trễ | **Chưa chạy với large-v3** | Thiếu weights; 4 integration cần speech được deselect, không tính pass |

Đo small mới trên cùng ba mẫu FLEURS local/reference hiện có; CPU/int8/4 threads, beam=3, VAD window=15, language tự phát hiện, `condition_on_previous_text=False`, timeout 120 giây/mẫu. Không dùng TTS/audio lặp làm bằng chứng chất lượng. Thời gian là toàn subprocess gồm cold start/import/model load/IPC:

| Mẫu | small WER | small CER | small giây | large-v3 WER/CER/giây |
|---|---:|---:|---:|---|
| Việt `vi_vn`, id 1886 | 4,35% | 1,56% | 9,050 | Chưa đo |
| Pháp `fr_fr` | 10,53% | 5,49% | 7,093 | Chưa đo |
| Nhật `ja_jp` | Không dùng WER cách trắng | 4,44% | 7,267 | Chưa đo |

Report `.test-tmp/large-v3/small.json` có hash audio, reference, hypothesis, segments, provider và timing. Tiếng Việt small nhận “vật” thành “bật”; tiếng Pháp nhận “ait été” thành “était”; tiếng Nhật nhận “敵対的” thành “適対的”, “住所” thành “重所”. Đây là quan sát ASR, không phải lỗi dịch Argos. Không suy rộng từ một mẫu/ngôn ngữ. Có `en.flac` cho smoke speech, nhưng chưa có reference tiếng Anh local trong định dạng evaluator; chưa đo WER/CER Anh hoặc mẫu chuyển ngôn ngữ có reference. Chưa tìm được fixture 20–30 phút phù hợp; **chưa kiểm chứng hiệu năng/độ đầy đủ video 30 phút**, chưa đo peak RAM. Không tăng timeout hay đổi chia cửa sổ vì chưa có bằng chứng đo thật.

**Tiếp tục:** dùng lệnh PowerShell tải và kiểm tra checksum tại [README mục 1.3a](README.md#13a-cài-môi-trường-mp3mp4-whisper-và-dịch-cục-bộ), rồi chạy evaluator small/large trên cùng mẫu. Nếu Xet tiếp tục đứng, đặt `$env:HF_HUB_DISABLE_XET="1"` trước lệnh tải. Sau khi đủ model, chạy toàn integration với `$env:ETL_MEDIA_REAL_TESTS="1"`, `$env:ETL_TEST_WHISPER_MODEL="D:/HK7/KLTN/ETL/Models/faster-whisper-large-v3"`, `$env:HF_HUB_OFFLINE="1"` và `.\.venv\Scripts\python.exe -m pytest tests/integration/test_media_local.py -q --basetemp=.test-tmp/large-v3/real-ready`. Các DB integration vẫn là fixture cô lập. Chỉ restart API/worker theo README khi model sẵn sàng; hiện chưa thể xác nhận nâng cấp vận hành hoàn tất. Nếu cần chạy ASR ngay, đổi đường dẫn về `Models/faster-whisper-small` rồi restart API/worker, không xóa large-v3.

## Bằng chứng lịch sử trước nâng cấp large-v3

Đã triển khai và kiểm chứng luồng local cốt lõi. **Chưa tuyên bố nghiệm thu toàn bộ**: nhánh thị giác chưa có model; PostgreSQL/MongoDB thật, Docker và một số trường hợp chất lượng chưa được kiểm chứng. Bộ kiểm tra toàn repository còn lỗi nền được ghi bên dưới.

## Cập nhật ưu tiên âm thanh và chất lượng ASR

- MP4 có track âm thanh bỏ toàn bộ bước lấy frame/OCR, kể cả track im lặng hoặc ASR lỗi. MP4 không có track âm thanh vẫn OCR nếu được bật. Phụ đề có sẵn tiếp tục được giữ riêng.
- Model đang cấu hình trong `.env`: `Models/faster-whisper-small`, CPU/int8/4 threads. Revision `536b0662742c02347bc0e980a01041f333bce120`; SHA-256 `model.bin`: `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671`. Model base vẫn được giữ.
- So sánh cùng ba mẫu FLEURS cục bộ, cùng VAD/cửa sổ 15 giây/beam 3, tự nhận diện ngôn ngữ:

| Mẫu | Base WER theo khoảng trắng | Small WER theo khoảng trắng | Base CER | Small CER |
| --- | --- | --- | --- | --- |
| Việt | 17,39% | 4,35% | 9,38% | 1,56% |
| Pháp | 21,05% | 10,53% | 16,48% | 5,49% |
| Nhật | Không dùng | Không dùng | 13,33% | 4,44% |

Chỉ một mẫu ngắn/ngôn ngữ; không suy rộng thành cam kết độ chính xác cho mọi học liệu. WER tiếng Việt ở đây tách token theo khoảng trắng. Small mất khoảng 5–7,5 giây/mẫu, base khoảng 2,7–3,6 giây/mẫu trên máy hiện tại. Báo cáo: `.test-tmp/asr-before-20261026.json`, `.test-tmp/asr-small-20261026.json`. Thử tăng beam hoặc bỏ VAD lần hai chưa cho cải thiện ổn định, nên giữ cách giải mã cũ. Cần khởi động lại API/worker để nạp code/model mới; kết quả đã lưu không tự được trích xuất lại.

## Chức năng và tệp chính

| Phần | Tệp |
| --- | --- |
| ffprobe, FFmpeg, phụ đề, OCR, chọn track, frame/timestamp | `src/data_sync_etl/adapters/media.py` |
| Whisper/VAD và dịch Argos trong tiến trình con, offline | `src/data_sync_etl/adapters/media_runtime.py` |
| ID đoạn, bản nguyên ngữ/bản dịch, lịch sử, checkpoint và export | `src/data_sync_etl/application/bilingual.py` |
| Admission media dùng khóa hiện có | `src/data_sync_etl/application/media_capacity.py` |
| Tích hợp worker/finalization/postprocess/search | `application/ocr.py`, `processing_finalizer.py`, `processing_state.py`, `postprocess.py`, `indexing.py`, `search.py` dưới `src/data_sync_etl/` |
| Upload có kiểm tra nội dung, bảo toàn staging/tệp gốc | `application/create_resource.py`, `application/create_resource_version.py`, `adapters/storage/local.py` |
| API/quyền truy cập | `api/routes.py`, `api/admin_access.py`, `security/authorization.py`, `api/search_schemas.py` |
| Cấu hình/wiring/schema | `config.py`, `composition.py`, `db/models.py`, `alembic/versions/0018_bilingual_media.py`, `.env.example` |
| UI upload, lựa chọn, đối chiếu, player và download | `frontend/src/components/BilingualPanel.tsx`, `ProcessingPreferences.tsx`, `frontend/src/services/processingPreferences.ts`, ba trang `LibraryCreatePage`, `LibraryUpdateVersion`, `LibraryProcessingPage` |
| Kiểm thử bổ sung | `tests/unit/test_bilingual_media.py`, `tests/integration/test_media_local.py`; cập nhật test chính sách media cũ |
| Đánh giá/tải dữ liệu kiểm thử công khai theo yêu cầu | `scripts/evaluate_media_asr.py`, `scripts/prepare_media_test_assets.py` |

Tệp gốc LOCAL vẫn lưu content-addressed; SOURCE vẫn dùng resolver/tham chiếu và chính sách cũ. Không chép đè tệp nguồn, không deploy production. Ở lần triển khai ban đầu không sửa `.env`/DB thật; bước kiểm tra tiếp theo ngày 26/10/2026 đã chú thích `.env` (giữ giá trị có hiệu lực) và áp dụng `0017` vào MASTER local/dev. `.worktrees/` đang có không bị sửa.

Dependencies/model/config/migration và API đầy đủ: [MEDIA_PIPELINE.md](MEDIA_PIPELINE.md). Đã cài thêm runtime trong `.venv`, chuẩn bị Whisper base đa ngôn ngữ và Argos Anh–Việt trong `Models/`, FFmpeg kiểm thử trong `.test-tmp/`; các thư mục này không đưa vào Git.

## Kết quả kiểm thử

| Kiểm tra | Kết quả | Phạm vi/giới hạn |
| --- | --- | --- |
| Regression liên quan cuối: upload, worker, retry, tài liệu, metadata media, search/access/context, schema | **290 pass, 3 skip**, 127,72 s | SQLite cô lập, Mongo memory store; embedding dùng test double rõ ràng |
| Tích hợp media/model thật cuối | **9 pass**, 36,63 s | FFmpeg, ffprobe, Tesseract, Whisper base và Argos thật; API TestClient thật; DB/Mongo/embedding như dòng trên |
| Unit song ngữ và context chạy lại sau sửa cuối | **36 pass**, 18,31 s | Không thay dữ liệu model bằng mock để báo chất lượng |
| Toàn unit suite ở vòng kiểm tra rộng | 1.755 pass, 9 skip, 19 fail; 817,53 s | Chạy trước các bản sửa cuối; không được tính là toàn bộ suite đã xanh |
| Frontend TypeScript/Vite | **Pass** | Build thực tế; bundle có cảnh báo kích thước; chưa thao tác UI trong trình duyệt |
| Frontend lint | Exit 0, còn cảnh báo nền | Không khẳng định toàn repo sạch warning |
| Kiểm thử cache frontend | **2 pass** | Node test runner |
| Ruff trên tệp Python thay đổi/thêm | **Pass** | Toàn repo vẫn có 20 lỗi ở các tệp reconciliation/demo không sửa trong công việc này |
| Ruff format toàn repository | **Chưa đạt**: 337 tệp cần format | Đã format các module/test/script mới; không reformat toàn bộ codebase ngoài phạm vi |
| `pip check` | **Pass** | Không có dependency bị hỏng sau cài đặt |
| `git diff --check`, parse Compose YAML | **Pass** | Không phải Docker build/runtime test |
| Pipeline PostgreSQL/MongoDB thật và Docker media | **Chưa chạy end-to-end** | MASTER local/dev đã xác minh/apply `0017`; không ghi fixture kiểm thử vào DB đang dùng |
| Mô tả nội dung video bằng VLM | **Chưa khả dụng** | Không có model thị giác; API/UI ghi rõ, không sinh mô tả giả |

Ba test skip trong nhóm regression được giữ nguyên điều kiện môi trường của bộ test; không sửa hoặc bỏ để làm pass. Trong test dịch lỗi/retry, model mock có nhãn `mock` và chỉ kiểm tra liên kết/checkpoint; các test tích hợp và số đo chất lượng dùng model thật.

Các lỗi rộng đã phân loại:

- Test cũ yêu cầu mọi MP3/MP4 bị từ chối không còn phù hợp yêu cầu mới. Đã thay bằng kiểm tra dữ liệu media sai, legacy content, bảo toàn file và acceptance media hợp lệ; tất cả test này nằm trong nhóm 290 pass.
- Migration downgrade và dependency direction do phần mở rộng đã được sửa; callback ffprobe/model được inject từ composition. Lỗi architecture còn lại trỏ đến `application/file_reconciliation.py`, tệp không sửa trong công việc này.
- Test số bảng Auth và số cột MASTER đã thất bại trên bản HEAD trước thay đổi: kỳ vọng cũ 18 bảng/20 cột trong khi repo đã có thêm reconciliation/file-size. Migration `0017` bổ sung cột được ghi rõ, không che thay đổi bằng backfill.
- Test capabilities thiếu `resource_format` và phép thử cache chờ event 10 giây cũng tái hiện thất bại trên HEAD trước thay đổi. Chưa sửa các chức năng nghiên cứu/cache ngoài phạm vi.
- Snapshot governance đóng băng ở MASTER `0015`; repo đã có `0016` trước phiên này, bản mở rộng thêm `0017`. Checker báo drift; chưa cập nhật/ghi đè baseline để ép pass.
- Một test trusted lexical export ở lượt rộng bị ảnh hưởng bởi `--basetemp` đặt dưới `.test-tmp`, chính là vùng xuất được phép. Đã chạy lại với basetemp dưới `storage/`; test pass trong nhóm cuối.

Log chi tiết ở `.test-tmp/media-unit-all.log`, `media-baseline-tests.log`, `media-verification-final.log`, `media-real-complete.log`, `media-lint-all.log`, `media-build-final.log`. Đây là artifact local, không đưa vào Git.

Sau lượt 9 integration pass, test OCR/search được tăng cường bằng slide có dấu **“Bài học phân số”** và chạy lại thành công. Kiểm tra cuối sau format: 17 test song ngữ pass; test OCR Unicode/search pass riêng. Log: `media-formatted-final.log`, `media-format-real.log`.

## Ba vòng đã dùng

| Vòng | Viết code | Kiểm thử/tìm lỗi | Sửa và kiểm tra lại |
| --- | --- | --- | --- |
| 1 | Kiểm tra media, worker, lưu đoạn, dịch, API, UI cơ bản | Regression tài liệu/pipeline và build | Xung đột import package `tests`, temp path trong sandbox, lượt dịch phục hồi dư; nhóm ban đầu 103 pass/3 skip |
| 2 | Retry/checkpoint, source locator, track, scene sampling, kiểm thử model thật | Dữ liệu công khai Việt/Anh, phụ đề/OCR/silence, đánh giá 3 ngôn ngữ | Pin PyAV tương thích; sửa bỏ mất câu khi dịch; ghép VAD để giữ ngữ cảnh; không loại nhầm slide đổi chữ bằng thumbnail |
| 3 | Tài liệu, migration tương thích, tích hợp bag/search và ổn định | Acceptance API/model thật, code switching, quyền, regression rộng | Sửa probe dùng đuôi staging; kiểm tra nội dung khi tạo phiên bản; dùng bag text hiện có; giữ offset track audio muộn; chạy lại 290 unit/regression và 9 integration |

Không tạo vòng thứ tư. Các kiểm tra lại sau sửa thuộc bước sửa lỗi của cùng vòng.

## Số đo thực tế

Máy kiểm thử: i5-12500H, RAM khoảng 16 GB, RTX 3050 4 GB. **Các số đo dưới dùng CPU/int8, 4 threads**, không dùng GPU. Có test khác chạy đồng thời; thời gian không phải benchmark tải sản xuất. Chưa đo peak RAM/VRAM.

Whisper được tải từ `Systran/faster-whisper-base`, revision `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`; SHA-256 model binary theo metadata tải xuống: `d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9`.

| Tệp do test tạo/chuyển mã | Thời lượng | Worker gồm trích xuất/dịch/finalization |
| --- | ---: | ---: |
| Video không audio có slide (bản ASCII ở lượt đo) | 4,00 s | 1,421 s |
| Video phụ đề Unicode, không audio | 3,00 s | 0,122 s |
| MP3 tiếng Việt | 11,412 s | 5,123 s |
| MP3 tiếng Anh | 11,050 s | 3,750 s |
| MP4 tiếng Anh | 15,00 s | 3,492 s |
| MP3 chuyển Việt → Anh | 22,428 s | 5,597 s |
| MP4 audio bắt đầu muộn | 15,00 s | 3,575 s |

Video/slide/silence được tạo bằng FFmpeg/Pillow để kiểm tra xử lý thật; không phải bằng chứng chất lượng trên video tự nhiên. Audio người nói dùng mẫu JFK từ bộ test faster-whisper và mẫu FLEURS công khai.

| Google FLEURS, một mẫu/ngôn ngữ | Thời lượng | Thời gian ASR | WER theo token khoảng trắng | CER |
| --- | ---: | ---: | ---: | ---: |
| Việt, sample 1886 | 11,34 s | 4,546 s | 17,39% | 9,38% |
| Pháp, sample 1987 | 10,20 s | 3,730 s | 21,05% | 16,48% |
| Nhật, sample 1828 | 10,44 s | 4,114 s | Không dùng tokenizer khoảng trắng | 13,33% |

Chuẩn hóa: casefold, bỏ dấu câu; CER bỏ khoảng trắng; tiếng Việt WER dùng token cách trắng nên gần lỗi âm tiết hơn lỗi từ đã phân tách. Mỗi ngôn ngữ chỉ một mẫu, **không suy rộng chất lượng**. Có lỗi từ vựng/số liệu trong transcript; không sửa nguyên ngữ bằng phỏng đoán. Report JSON có SHA-256 audio, reference, hypothesis, timestamp và provider tại `.test-tmp/media-quality.json`.

Tài liệu API đã đối chiếu với [faster-whisper 1.2.1](https://github.com/SYSTRAN/faster-whisper/tree/v1.2.1), [ffprobe](https://ffmpeg.org/ffprobe.html); dữ liệu đánh giá từ [Google FLEURS](https://huggingface.co/datasets/google/fleurs). Các tải xuống chỉ lấy dependency/model/dữ liệu công khai; không gửi tài liệu người dùng ra ngoài.

## Ví dụ liên kết thực tế

Đây là một đoạn từ DB cô lập của test `delayed.mp4`; ID chỉ là ID artifact kiểm thử:

```json
{
  "segment_id": "f3addba062349fced38505bb21",
  "source_type": "speech",
  "source_language": "en",
  "target_language": "vi",
  "start_time": 1.976009,
  "end_time": 12.872009,
  "original_text": "And so my fellow Americans, ask not what your country can do for you, ask what you can do for your country.",
  "translated_text": "Và vì vậy, những người bạn Mỹ của tôi, không hỏi những gì đất nước của bạn có thể làm cho bạn, hỏi những gì bạn có thể làm cho đất nước của bạn.",
  "translation_status": "completed"
}
```

Kiểm tra dịch hai câu riêng với model thật đã giữ số `25` và mã `ETL-123`; chưa đánh giá dịch hệ thống trên corpus nhiều miền hoặc mọi cặp ngôn ngữ.

## Lệnh kiểm chứng

PowerShell; test không dùng DB production:

```powershell
& .venv/Scripts/python.exe -m pytest tests/unit/test_bilingual_media.py tests/unit/test_media_out_of_scope.py tests/unit/test_process_version.py tests/unit/test_search_context.py -q --basetemp=storage/media-check-local
$env:ETL_MEDIA_REAL_TESTS='1'
& .venv/Scripts/python.exe -m pytest tests/integration/test_media_local.py -q --basetemp=storage/media-check-real
& .venv/Scripts/python.exe scripts/evaluate_media_asr.py --model Models/faster-whisper-base
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run lint
node --test tests/frontend/test_metadata_search_cache.mjs
& .venv/Scripts/python.exe -m pip check
```

Test thật dùng binary/model đã chuẩn bị ở vị trí mô tả trong `MEDIA_PIPELINE.md`; có thể ghi đè bằng `ETL_TEST_FFMPEG`, `ETL_TEST_FFPROBE`, `ETL_TEST_TESSERACT`, `ETL_TEST_WHISPER_MODEL`. Script đánh giá đọc các file local, không tải mạng. Để tái chuẩn bị mẫu công khai trên máy khác:

```powershell
& .venv/Scripts/python.exe -m pip install pyarrow fsspec aiohttp
& .venv/Scripts/python.exe scripts/prepare_media_test_assets.py --download-public-samples
```

FLEURS có row group lớn; đọc một mẫu có thể truyền cả cột audio hàng trăm MB/ngôn ngữ. Script bỏ qua mẫu đã có. Các dependency này chỉ phục vụ tải/đọc dữ liệu kiểm thử, không bắt buộc cho API.

## Giới hạn và việc chưa hoàn thành

- Không có mô tả hình ảnh bằng VLM đã hoạt động/được kiểm chứng. API chỉ báo trạng thái chưa khả dụng khi bật.
- Phụ đề bitmap không được giải mã/OCR trực tiếp từ track riêng; OCR khung video hoạt động, cảnh báo nêu rõ track ảnh cần xử lý thêm.
- Chuyển ngôn ngữ giữa các cửa sổ đã kiểm chứng; câu trộn nhiều ngôn ngữ trong cùng cửa sổ có thể sai hoặc mang `und`. Chưa nghiệm thu corpus code switching tự nhiên, nhạc/nhiễu và mọi hệ chữ OCR.
- Chỉ có gói dịch Anh–Việt đã chuẩn bị và kiểm chứng. Các cặp khác cần cài gói phù hợp; thiếu gói trả lỗi từng đoạn, nguyên ngữ vẫn còn. Chưa có glossary hoặc kiểm chứng bảo toàn mọi thuật ngữ/số liệu.
- Retry dịch phục hồi từng đoạn và giữ lịch sử; retry extraction vẫn dùng task hiện tại. Với tài liệu/media thành công một phần, chưa có cơ chế chỉ chạy lại riêng một nhánh ASR/OCR đã lỗi mà giữ các nhánh khác như một workflow độc lập.
- Scratch FFmpeg/model được dọn bằng temporary context. Nếu lỗi xảy ra sau khi ảnh dẫn xuất đã được publish, có thể còn blob chưa tham chiếu; không tự xóa blob content-addressed vì upload khác có thể đã dùng chung. Việc dọn blob này cần inventory/reconciliation, không xóa tự động trong worker.
- Model/dữ liệu nhiều giờ, OOM/GPU, phục hồi do tiến trình bị kill thật, phân tán PostgreSQL advisory locks và Mongo thật chưa được kiểm chứng. Test khởi tạo lại service dùng checkpoint DB đã lưu, không thay thế thử nghiệm kill/restart production.
- Search chỉ index nguyên ngữ. Cơ chế review và index cũ được giữ. Không bổ sung hệ thống hỏi đáp mới vì dự án hiện có retrieval/context, chưa có luồng QA cần mở rộng trong phạm vi này.
- Chưa kiểm tra tương tác UI bằng browser; TypeScript/build/lint đã chạy. Chưa build Docker image media. Toàn bộ test/check repository chưa xanh do các lỗi nền và snapshot governance nêu trên.

Để nghiệm thu đầy đủ: giải quyết/cập nhật contract nền theo quyết định dự án, chạy trên PostgreSQL/MongoDB cô lập, kiểm tra UI, bổ sung corpus chất lượng và quyết định triển khai VLM/bitmap-subtitle/branch retry. Hướng dẫn kiểm tra thủ công một luồng đầy đủ nằm cuối [MEDIA_PIPELINE.md](MEDIA_PIPELINE.md).


## Cập nhật kiểm tra cấu hình ngày 26/10/2026

- Các biến media người dùng đã thêm được Settings nạp thành công; FFmpeg 6.0, ffprobe và Tesseract `vie+eng` chạy được; đủ tệp Whisper, gói Argos Anh–Việt có model.
- Bổ sung mô tả bằng tiếng Việt UTF-8 vào `.env`; gom ba biến OCR về một khai báo. Đường dẫn Tesseract lấy đúng giá trị khai báo cuối trước khi gom. Đã đối chiếu toàn bộ giá trị dotenv trước/sau: không đổi.
- MASTER được xác minh là PostgreSQL local, môi trường development, revision `0016`, không phải SOURCE. Đã áp dụng riêng `0017` trong transaction có lock timeout/statement timeout; hai cột mới nullable, không backfill dữ liệu. Snapshot schema trước nâng: `.test-tmp/media-readiness/schema-before-0017.json` (không phải bản sao lưu toàn DB).
- Model smoke test dùng trực tiếp đường dẫn từ `.env`: Whisper nhận speech tiếng Anh với timestamp hợp lệ; Argos dịch hai câu và giữ số `25`, mã `ETL-123`. Artifact: `.test-tmp/media-readiness/configured-model-check.json`.
- Chạy lại toàn bộ integration media với FFmpeg/ffprobe/Tesseract/Whisper từ cấu hình hiện tại: **9 pass trong 32,98 giây**. DB/Mongo/embedding vẫn là fixture cô lập; không chèn mẫu vào MASTER đang dùng. Log: `.test-tmp/media-readiness/integration-configured.log`.
- Hướng dẫn cài môi trường/model được chuyển về README mục 1.3a; API và hành vi xử lý tiếp tục ở MEDIA_PIPELINE.md.
- Cần restart API/worker đang chạy để nạp cấu hình và code mới. Các giới hạn VLM, bitmap subtitle, branch retry và kiểm chứng pipeline trên PostgreSQL/MongoDB thật vẫn giữ như phần trên.
