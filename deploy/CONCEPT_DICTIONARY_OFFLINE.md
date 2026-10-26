# Từ điển corpus — vận hành thử nghiệm offline

Chức năng này đọc các collection đã có qua read capability; không tạo/sửa MongoDB hoặc SOURCE/MASTER. Phần vận hành thật vẫn bị chặn vì chưa có credential RO, complete revocation feed và artifact volume được xác minh. `--live` trả exit2 trước khi mở secret/DB/root.

Artifact SQLite là **build output bất biến ngoài Mongo/PostgreSQL**, không phải nguồn học liệu hoặc nơi quản lý account. Candidate contributions/checkpoint và review ledger nằm riêng tại private root. API đọc shard với `mode=ro&immutable=1`; không nạp 100k concept vào Dictionary hoặc quét corpus theo request.

## 1. Tạo nguồn giả lập và chạy discovery

PowerShell, sau khi kích hoạt venv:

```powershell
$conceptRoot = Join-Path (Get-Location) '.test-tmp/concept-demo'
python -m data_sync_etl.cli.concepts example --root $conceptRoot
python -m data_sync_etl.cli.concepts run --root $conceptRoot --fixture "$conceptRoot/example-corpus.json" --loop
```

Lệnh example tạo 35 resource VI/EN/FR, trong đó có resource không public. Không chạy lại example lên file cũ. Worker xử lý từng batch, ghi contribution trước checkpoint, rewind sau mỗi inventory sweep, không OCR/tải media. Ctrl+C dừng worker. File fixture sửa đổi được đọc lại giữa các tick. Khi fixture không đọc được, worker chờ thay vì xử lý từ authority cũ.

Trên Linux, dùng cùng CLI với `--root` là đường dẫn tuyệt đối; có thể lấy bằng `realpath .test-tmp/concept-demo` sau khi tạo thư mục trống. Không dùng thư mục frontend/public/static hoặc dữ liệu vận hành để thử.

## 2. Kiểm tra và duyệt rõ ràng một mapping

```powershell
python -m data_sync_etl.cli.concepts status --root $conceptRoot
$items = python -m data_sync_etl.cli.concepts candidates --root $conceptRoot --fixture "$conceptRoot/example-corpus.json" | ConvertFrom-Json
$candidate = $items.relations[0]
$candidate.terms
$reviewerId = Read-Host 'Mã người duyệt thực tế, không dùng email'
```

Chỉ sau khi kiểm tra cặp thuật ngữ và dẫn chứng fixture, ghi ba quyết định riêng:

```powershell
python -m data_sync_etl.cli.concepts review --root $conceptRoot --fixture "$conceptRoot/example-corpus.json" --candidate-id $candidate.id --binding $candidate.binding --kind term --term-index 0 --decision approved --reviewer $reviewerId
python -m data_sync_etl.cli.concepts review --root $conceptRoot --fixture "$conceptRoot/example-corpus.json" --candidate-id $candidate.id --binding $candidate.binding --kind term --term-index 1 --decision approved --reviewer $reviewerId
python -m data_sync_etl.cli.concepts review --root $conceptRoot --fixture "$conceptRoot/example-corpus.json" --candidate-id $candidate.id --binding $candidate.binding --kind relation --decision approved --reviewer $reviewerId
```

Dùng `--decision rejected` để từ chối. Không có lệnh auto-approve hàng loạt. Cùng relation ID ở nhiều version có binding khác nhau: chọn đúng `--binding`. Thuật ngữ xuất hiện gần nhau hoặc cosine cao không tự thành đồng nghĩa. IDs reviewer chỉ là khai báo operator trong ledger offline, chưa phải hệ thống xác thực reviewer cho production.

## 3. Build, kiểm tra và kích hoạt bản fixture

```powershell
$release = python -m data_sync_etl.cli.concepts build --root $conceptRoot --fixture "$conceptRoot/example-corpus.json" | ConvertFrom-Json
python -m data_sync_etl.cli.concepts validate --root $conceptRoot --generation $release.generation
python -m data_sync_etl.cli.concepts publish --root $conceptRoot --fixture "$conceptRoot/example-corpus.json" --generation $release.generation
```

Build không tự đổi current pointer. Publish này **chỉ kích hoạt artifact mang scope offline_fixture**. Composition thật không cài complete authority attestor hoặc `allow_offline`; kể cả bật cờ, Search vẫn dùng seed/literal và trạng thái BLOCKED_LIVE.

Rollback dùng cùng CLI `rollback --generation ... --fixture ...`; bị từ chối nếu source/review token đã đổi. Không phục hồi alias bị thu hồi bằng cách trỏ lại generation cũ. `reconcile` rewind inventory cursor; không xóa review history hoặc file học liệu. Xóa mềm/đổi quyền/source edit/review reject làm fixture fence đổi; resolver bỏ alias cũ, request đang chạy tính lại seed; HTTP final-send guard chặn alias bị thu hồi sau ranking.

## 4. Kiểm tra quy mô

```powershell
python scripts/benchmark_concept_artifacts.py --root "$((Get-Location).Path)/.test-tmp/concept-scale-new" --sizes 10000 100000
```

Chọn root mới; không overwrite evidence. Benchmark dùng approved **synthetic inputs**, không chứng minh reviewer/quality thực tế. Đo disk, CPU, peak RSS, cold verify, lookup P50/P95/P99. Không download model. Bộ benchmark HTTP riêng dùng embedding test double; không dùng các số liệu này để quảng bá latency BGE-M3 production.

## Điều kiện chưa cho rollout

- Credential đọc-chỉ và projection/index đã kiểm chứng; không admin probe.
- Complete authority/revocation contract bao phủ unit review/retrieval, resource/version và Mongo extension edit; resource epoch hiện có không đủ.
- Volume private, ACL/owner, dung lượng/fsync/atomic rename, một publisher và cơ chế đa host được duyệt.
- Reviewer xác thực, qrels độc lập, staging/concurrency/soak và nghiệm thu chất lượng.

Chưa triển khai GC tự động hoặc shared multi-host fencing; giữ releases/evidence, dừng khi disk budget/free-space không đáp ứng. Discovery incremental theo contribution/fingerprint; publish hiện build canonical shards từ stream approved, chưa tối ưu reuse shard khi chỉ 1% thay đổi. Không coi polling sweep là thu hồi tức thì trên hệ thống thật.
