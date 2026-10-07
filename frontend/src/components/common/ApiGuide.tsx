import { useState } from 'react';
import { api } from '../../services/apiClient';
import type { ApiResponse } from '../../services/apiClient';
import { useEtl } from '../../context/EtlContext';

const endpoints = [
  { name: '1. Kiểm tra kết nối', method: 'GET', path: '/health', hint: 'Kiểm tra ứng dụng và kết nối PostgreSQL.' },
  { name: '2. Đồng bộ Master Data', method: 'POST', path: '/admin/sync/master', hint: 'Lần đầu chọn Full; các lần sau chọn Incremental. Nguồn hiện tại: fixtures/source.' },
  { name: '3. Tiếp nhận PDF', method: 'POST', path: '/admin/resources/{resource_id}/versions/ingest', hint: 'Đặt PDF trong storage/{bucket}/{object_key} trước. API tiếp nhận tệp có sẵn, chưa upload từ trình duyệt.' },
  { name: '4. Tạo OCR job', method: 'POST', path: '/admin/resource-versions/{version_id}/ocr-jobs', hint: 'Dùng Version ID trả về từ bước 3. Sau đó chạy worker bằng terminal.' },
  { name: '5. Hậu xử lý', method: 'POST', path: '/admin/resource-versions/{version_id}/postprocess', hint: 'Chạy sau khi OCR job đạt COMPLETED hoặc COMPLETED_WITH_ERRORS.' },
  { name: '6. Xem trạng thái ETL', method: 'GET', path: '/admin/resource-versions/{version_id}/etl-status', hint: 'Xem tiến độ OCR, lịch sử hậu xử lý và số content units. Có thể chạy lại để cập nhật.' },
] as const;

export function ApiGuide() {
  const { isLiveMode, selectedVersionId, fetchEtlStatus } = useEtl();
  const [step, setStep] = useState(selectedVersionId ? 3 : 0);
  const [resourceId, setResourceId] = useState('resource-1');
  const [versionId, setVersionId] = useState(selectedVersionId || '');
  const [bucket, setBucket] = useState('input');
  const [objectKey, setObjectKey] = useState('book.pdf');
  const [stream, setStream] = useState('all');
  const [full, setFull] = useState(true);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ApiResponse<unknown> | null>(null);
  const endpoint = endpoints[step];
  const path = endpoint.path.replace('{resource_id}', encodeURIComponent(resourceId.trim())).replace('{version_id}', encodeURIComponent(versionId.trim()));
  const body = step === 1 ? { stream, full } : step === 2 ? { bucket: bucket.trim(), object_key: objectKey.trim() } : step === 3 ? {} : null;

  async function run() {
    setBusy(true);
    setResult(null);
    try {
      let response: ApiResponse<any>;
      if (step === 0) response = await api.getHealth();
      else if (step === 1) response = await api.runMasterSync(stream, full);
      else if (step === 2) response = await api.ingestResource(resourceId.trim(), bucket.trim(), objectKey.trim());
      else if (step === 3) response = await api.createOcrJob(versionId.trim());
      else if (step === 4) response = await api.runPostprocess(versionId.trim());
      else response = await api.getEtlStatus(versionId.trim());
      setResult(response);
      if (response.ok && step === 2 && response.data?.version?.id) setVersionId(response.data.version.id);
      // Read the status once after writes to populate the existing workflow screens.
      if (response.ok && step >= 2) {
        const id = step === 2 ? response.data?.version?.id : versionId.trim();
        if (id) await fetchEtlStatus(id);
      }
    } finally {
      setBusy(false);
    }
  }

  // This panel sends real requests only; prototype actions remain on their own screens.
  const missing = step === 2 ? !resourceId.trim() || !bucket.trim() || !objectKey.trim() : step >= 3 ? !versionId.trim() : false;
  return <div className="card api-guide">
    <div className="card-header"><div>
      <div className="card-title">Thử đầy đủ 6 API</div>
      <div className="card-subtitle">Chọn bước, kiểm tra request rồi gửi. Các lệnh POST thực hiện thay đổi trong CSDL.</div>
    </div></div>
    <div className="api-steps">{endpoints.map((item, index) => <button key={item.path} type="button" className={`btn ${step === index ? 'btn-primary' : 'btn-secondary'} btn-sm`} disabled={busy} onClick={() => { setStep(index); setResult(null); }}>{item.name}</button>)}</div>
    <p>{endpoint.hint}</p>
    {!isLiveMode && <p className="notice-box warning">Chọn Live Backend API ở thanh phía trên để gửi request thật.</p>}
    <fieldset disabled={busy} className="api-fields">
      {step === 1 && <><label>Stream<select className="form-select" value={stream} onChange={e => setStream(e.target.value)}>{['all', 'grades', 'subjects', 'users', 'resources'].map(s => <option key={s}>{s}</option>)}</select></label><label>Chế độ<select className="form-select" value={String(full)} onChange={e => setFull(e.target.value === 'true')}><option value="true">Full — toàn bộ</option><option value="false">Incremental — tăng dần</option></select></label></>}
      {step === 2 && <><label>Resource ID<input className="form-input" value={resourceId} maxLength={30} onChange={e => setResourceId(e.target.value)} /></label><label>Bucket<input className="form-input" value={bucket} maxLength={255} onChange={e => setBucket(e.target.value)} /></label><label>Object key<input className="form-input" value={objectKey} maxLength={500} onChange={e => setObjectKey(e.target.value)} /></label></>}
      {step >= 3 && <label>Version ID thật<input className="form-input mono" value={versionId} maxLength={30} placeholder="Dán ID trả về từ ingest hoặc CLI" onChange={e => setVersionId(e.target.value)} /></label>}
    </fieldset>
    <pre className="api-json">{endpoint.method} {path}{body ? `\n${JSON.stringify(body, null, 2)}` : '\nKhông có request body'}</pre>
    <button type="button" className="btn btn-primary" disabled={busy || missing || !isLiveMode} onClick={run}>{busy ? 'Đang gửi…' : `Gửi ${endpoint.method}`}</button>
    {step === 3 && <pre className="api-json">etl run-ocr-worker --worker-id worker-1</pre>}
    <div aria-live="polite">{result && <><p><strong>{result.ok ? 'Thành công' : 'Không thành công'} — HTTP {result.status || 'không kết nối'}</strong>{result.error && `: ${result.error}`}</p><pre className="api-json">{JSON.stringify(result, null, 2)}</pre></>}</div>
    <p>HTTP 403: bật ADMIN_ENABLED=true và khởi động lại backend. PDF scan cần OCR engine thật; worker và recovery hiện dùng CLI.</p>
  </div>;
}
