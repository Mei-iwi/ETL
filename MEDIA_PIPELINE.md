# Media và nội dung song ngữ

Phần mở rộng dùng FastAPI, worker OCR, PostgreSQL và MongoDB đang có. Không có hàng đợi mới, dịch vụ API ngoài hoặc model tự tải khi chạy. Ở bước kiểm tra tiếp theo ngày 26/10/2026, `.env` đã được chú thích/gom biến OCR trùng mà giữ nguyên giá trị có hiệu lực; MASTER PostgreSQL local/dev đã được nâng từ `0016` lên `0017` bằng transaction không backfill dữ liệu.

## Cài đặt local

Hướng dẫn cài thư viện, FFmpeg/ffprobe, Tesseract, tải Whisper/Argos, cấu hình biến môi trường và migration đã được tập trung tại **README.md — mục 1.3a**. Tiếp tục khởi động API/worker theo mục 1.7 trong [README.md](README.md).

Cấu hình nâng cấp: Whisper **large-v3 đầy đủ đa ngôn ngữ**, `Systran/faster-whisper-large-v3`, revision `edaa852ec7e145841d8ffdb056a99866b5f0a478`, đường dẫn `Models/faster-whisper-large-v3`, CPU/int8, 4 threads, 1 slot media. Weights đã tải đủ, checksum khớp và evaluator offline thành công. Base/small đã được xóa theo yêu cầu người dùng, muốn rollback phải tải lại; giữ Argos Anh → Việt. Tải và kiểm tra checksum/smoke test theo [README mục 1.3a](README.md#13a-cài-môi-trường-mp3mp4-whisper-và-dịch-cục-bộ); số đo thật trong MEDIA_ACCEPTANCE. Kết quả base/small cũ là lịch sử.

Runtime yêu cầu config, weights, tokenizer và vocabulary local trước khi gọi WhisperModel (`local_files_only=True`); thiếu/hỏng trả `STT_RUNTIME_UNAVAILABLE`, không tự tải tokenizer hoặc fallback sang small. Model đơn ngữ trả `STT_MULTILINGUAL_MODEL_REQUIRED`. Mặc định Settings vẫn để đường dẫn `None` an toàn khi chưa cài model. Không đổi schema DB cho nâng cấp này.

`SOURCE_METADATA_MEDIA_ENABLED=true` giữ chế độ đăng ký metadata-only tự động cho SOURCE. Dùng `false` nếu muốn worker SOURCE tự xếp hàng xử lý nội dung; quyền đọc và chính sách tệp tạm SOURCE vẫn phải được bật như trước.

## Migration và khởi động

Sau khi merge `main`, revision media là `0018`, nối tiếp revision `0017` xóa mềm của `main`. Migration media chỉ thêm hai cột nullable:

- `resource_versions.processing_options`: tên tệp gốc, tùy chọn và trạng thái dịch.
- `content_units.source_locator`: nguồn, timestamp/quan sát, vị trí tài liệu và ID đoạn liên kết.

Nguyên ngữ và bản dịch nằm riêng trong `ocr_page_results.result_metadata.paired_segments`; `raw_text` vẫn giữ kết quả trích xuất. Không cần thay enum/schema bag MongoDB: nội dung media dùng bag `TXT_TEXT`, phân biệt modality bằng `source_locator.source_type`.

Sau khi sao lưu và kiểm tra đúng DB local/dev, dừng API/worker rồi chạy:

```powershell
& .venv/Scripts/python.exe -m alembic current
& .venv/Scripts/python.exe -m alembic upgrade head
& .venv/Scripts/python.exe -m uvicorn data_sync_etl.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
# Terminal thứ hai
& .venv/Scripts/python.exe -m data_sync_etl.cli run-ocr-worker --worker-id ocr-1
# Terminal thứ ba
npm.cmd --prefix frontend run dev
```

Lịch sử trước merge: hai cột media từng được áp dụng vào MASTER local/dev với revision cũ `0017`. Revision này trùng số với xóa mềm trên `main`, nên DB cũ cần đối chiếu schema/lịch sử riêng trước khi nâng; không upgrade/stamp mù hoặc xóa cột media. Lượt merge chỉ kiểm thử trên DB cô lập, không chỉnh DB đang phục vụ. Hai cột nullable, không backfill; downgrade media `0018` từ chối nếu hai cột đã có dữ liệu.

Image `deploy/docker/backend.Dockerfile` có FFmpeg; truyền build arg `INSTALL_MEDIA=true` để cài ASR/dịch. Mount model offline và cấu hình đường dẫn tương ứng trong container. Docker build/run chưa được kiểm chứng trong phiên này.

## Luồng và dữ liệu

Upload kiểm tra chữ ký, dung lượng, container/codec/thời lượng bằng ffprobe trước khi công bố tệp. Mốc kiểm tra dùng định dạng khai báo, không suy từ tên tệp staging. Upload không đợi ASR/OCR/dịch.

Worker xử lý một task media qua cơ chế claim token, heartbeat, giới hạn retry và phục hồi cũ. PostgreSQL advisory locks giới hạn slot media giữa các worker; SQLite chỉ dùng khóa trong tiến trình và chỉ dành cho test/local. FFmpeg và model chạy với timeout; model ASR/dịch được cô lập bằng tiến trình con có thể kết thúc. Task đã commit kết quả trích xuất trước bước dịch. Kết quả từng đoạn dịch được checkpoint; yêu cầu dịch lại cập nhật cùng ID, lưu lịch sử bản dịch trước và không tạo thêm embedding.

Chọn track mặc định do container đánh dấu, sau đó chỉ số stream thấp nhất; lựa chọn thủ công dùng **absolute stream index** của ffprobe. Các track và lựa chọn được lưu. Audio tách 16 kHz mono; VAD giữ khoảng gốc, ghép các khoảng lời nói gần nhau trong cửa sổ giới hạn rồi nhận diện ngôn ngữ lại ở mỗi cửa sổ. Track audio bắt đầu muộn được cộng offset về timeline video. `und` là chưa xác định; điểm ASR không được gọi là xác suất chính xác của văn bản.

Giữ cửa sổ VAD/inference tối đa 15 giây (`ASR_WINDOW_SECONDS`, cho phép 2–30), không phải chunk quản lý job nhiều phút; VAD tìm điểm im lặng để chia, nhưng lời nói liên tục vượt giới hạn vẫn có rủi ro cắt từ. Chưa có bằng chứng audio dài để thay thuật toán/overlap. Giữ beam=3, `condition_on_previous_text=False`, VAD silence=300 ms, loại đoạn `no_speech_prob > 0.6` hoặc `avg_logprob < -1`. Ngôn ngữ người dùng chọn được truyền nguyên vẹn; tự nhận diện lại từng cửa sổ khi bỏ trống. Không dùng LLM sửa transcript, không coi điểm ASR là phần trăm đúng.

Decode toàn audio thành float32 16 kHz: 30 phút khoảng 115,2 MB mảng PCM (chưa gồm bộ đệm/model); không streaming/cache model toàn cục. Model nạp trong từng tiến trình con cô lập. Report đánh giá tách thời gian nạp model và decode/VAD/inference; thời gian toàn subprocess gồm cold start/import/IPC. Giữ giới hạn 50 MiB/1.800 giây, timeout media 900 giây bao gồm tách audio và ASR. ffprobe tối đa 30 giây; chờ slot media tối đa 900 giây riêng. Worker có heartbeat mỗi `OCR_HEARTBEAT_TIMEOUT_SECONDS/3` (mặc định 120/3 giây), không phải hard timeout 120 giây cho ASR. `subprocess.run(timeout=...)` dừng và chờ tiến trình con, scratch dùng TemporaryDirectory. Chưa đo video 30 phút nên không tăng timeout để suy đoán khả năng xử lý.

Phụ đề văn bản được lưu riêng với transcript. MP4 có track âm thanh sẽ **không lấy frame và không chạy OCR**, kể cả âm thanh im lặng hoặc nhận dạng thất bại; metadata ghi `frame_ocr_status=skipped_audio_present`. MP4 không có track âm thanh mới chạy OCR nếu được bật: lấy mẫu thời gian kết hợp scene detection, loại ảnh giống hệt và chữ lặp liên tiếp. Một thay đổi nhỏ của slide vẫn được OCR thay vì loại chỉ bằng thumbnail. OCR chỉ có `observed_at`, không tạo khoảng bắt đầu/kết thúc giả. Khung đã giữ có URL tải qua kiểm tra quyền của phiên bản.

Không âm thanh, im lặng, không phát hiện lời nói và không giải mã được có trạng thái riêng. Media hợp lệ nhưng không có nội dung trả `NO_EXTRACTABLE_CONTENT`. Thiếu runtime/model trả mã lỗi; có nhánh thành công thì giữ nội dung và ghi cảnh báo phần còn thiếu. Mô tả video hiện `disabled`/`unavailable`, chưa có model thị giác hoạt động.

Tài liệu PDF, DOCX, XLSX, TXT, CSV, PPTX và ảnh đang hỗ trợ dùng cùng cơ chế đoạn song ngữ; không thêm định dạng tài liệu mới. Nội dung tài liệu được coi là dữ liệu, không phải chỉ dẫn thực thi. Dịch cùng ngôn ngữ trả `same_language`. Gói dịch thiếu/lỗi không làm mất nguyên ngữ. Tài liệu cũ có thể yêu cầu dịch lại để bổ sung metadata song ngữ từ `raw_text` đã lưu.

Chỉ nguyên ngữ được lập chỉ mục bằng pipeline/BGE-M3 hiện tại. Bản dịch dùng cùng liên kết nguồn, nên không sinh thêm kết quả tìm kiếm trùng. Các kiểm tra review, ownership và visibility vẫn áp dụng; nội dung mới vẫn cần duyệt theo quy trình cũ. Kết quả và context trả `source_locator` cho media/tài liệu có locator; không trả bucket/object key của ảnh ra JSON.

## API

Các API mới dùng cùng cookie/CSRF và kiểm tra admin/quyền phiên bản. Ví dụ JSON body dưới đây chỉ là cấu hình, không phải dữ liệu kiểm thử model:

```json
{
  "source_language": null,
  "target_language": "vi",
  "ocr_enabled": true,
  "ocr_languages": "vie+eng",
  "audio_track": null,
  "subtitle_track": null,
  "visual_enabled": false
}
```

| Phương thức và đường dẫn sau `/api/v1/resource-versions/{id}` | Công dụng |
| --- | --- |
| `PUT /processing-options` | Lưu tùy chọn trước khi bắt đầu job |
| `POST /process` | Xếp hàng bằng API đang có |
| `GET /bilingual` | Nguyên ngữ/bản dịch, trạng thái, track, cảnh báo, nguồn |
| `POST /translations/retry` | Body chỉ gồm `source_language`, `target_language`; HTTP 202 |
| `GET /processing-source` | Xem tệp gốc, có hỗ trợ Range qua response hiện có |
| `GET /segments/{segment_id}/frame` | Khung nguồn nếu thật sự đã lưu |
| `GET /exports/json` | Metadata và các đoạn liên kết |
| `GET /exports/txt` | Nguyên ngữ; thêm `?translated=true` cho bản dịch |
| `GET /exports/srt` hoặc `/exports/vtt` | Chỉ lời nói/phụ đề có khoảng hợp lệ; thêm `?translated=true` nếu cần |

SRT/VTT ưu tiên track phụ đề đã chọn khi có cả phụ đề và speech. OCR/mô tả không được xuất thành phụ đề lời nói. Bản không tồn tại trả 404. Muốn đổi lựa chọn ASR sau khi đã trích xuất, dùng phiên bản tệp mới; sửa ngôn ngữ khi dịch lại chỉ sửa ngôn ngữ của bước dịch, không tự thay transcript.

## Kiểm tra thủ công

1. Đăng nhập bằng tài khoản được phép, tải MP3/MP4 hoặc tài liệu hỗ trợ; chọn ngôn ngữ và OCR trong form upload.
2. Mở trang xử lý. Theo dõi job thật, mở bản đối chiếu và tệp gốc. Với MP4 không audio, kiểm tra phụ đề/OCR cùng cảnh báo nếu có.
3. Bấm timestamp; player phải nhảy đúng vị trí. Với OCR, kiểm tra nhãn thời điểm quan sát và mở khung hình.
4. Tải TXT/JSON/SRT/VTT phù hợp. Đối chiếu `segment_id` và `original_text` trước/sau dịch lại.
5. Để kiểm tra lỗi dịch: đặt thư mục model dịch không tồn tại, restart worker và xử lý tệp khác ngôn ngữ đích. Nguyên ngữ vẫn xem/tải được. Khôi phục model, restart worker rồi yêu cầu dịch lại.
6. Duyệt content unit, lập chỉ mục bằng luồng cũ, tìm một cụm có thật trong nguyên ngữ và kiểm tra `source_locator` trong kết quả/context.

Kết quả thực tế, lệnh test và những phần chưa nghiệm thu đầy đủ: [MEDIA_ACCEPTANCE.md](MEDIA_ACCEPTANCE.md).
