import React, { createContext, useContext, useState, useEffect } from 'react';
import type {
  MasterLearningResource,
  ResourceVersion,
  OCRJob,
  OCRPageTask,
  OCRPageResult,
  ETLStageRun,
  ContentUnit,
  SyncRun,
  SyncCheckpoint,
  SystemHealth,
  MasterStream,
  IngestionResult,
} from '../types/etl';
import {
  INITIAL_HEALTH,
  INITIAL_RESOURCES,
  INITIAL_VERSIONS,
  INITIAL_JOBS,
  INITIAL_PAGE_TASKS,
  INITIAL_PAGE_RESULTS,
  INITIAL_STAGE_RUNS,
  INITIAL_CONTENT_UNITS,
  INITIAL_SYNC_RUNS,
  INITIAL_CHECKPOINTS,
} from '../data/mockData';
import { api } from '../services/apiClient';

export type ScreenId =
  | 'overview'
  | 'sync'
  | 'resources'
  | 'resource-detail'
  | 'ingest'
  | 'ocr'
  | 'operations'
  | 'postprocess'
  | 'content-units'
  | 'health';

export interface Notification {
  id: string;
  type: 'info' | 'success' | 'warning' | 'error';
  title: string;
  message: string;
  timestamp: string;
}

interface EtlContextType {
  // Navigation
  activeScreen: ScreenId;
  setActiveScreen: (screen: ScreenId, params?: { resourceId?: string; versionId?: string; jobId?: string }) => void;
  selectedResourceId: string | null;
  setSelectedResourceId: (id: string | null) => void;
  selectedVersionId: string | null;
  setSelectedVersionId: (id: string | null) => void;
  selectedJobId: string | null;
  setSelectedJobId: (id: string | null) => void;

  // Backend connection state
  isLiveMode: boolean;
  setIsLiveMode: (val: boolean) => void;
  apiBaseUrl: string;
  setApiBaseUrl: (url: string) => void;
  backendStatus: 'checking' | 'connected' | 'disconnected' | 'admin_disabled' | 'db_error';
  lastCorrelationId: string | null;
  health: SystemHealth;
  checkBackendHealth: () => Promise<void>;

  // Data Collections
  resources: MasterLearningResource[];
  versions: ResourceVersion[];
  ocrJobs: OCRJob[];
  pageTasks: OCRPageTask[];
  pageResults: OCRPageResult[];
  contentUnits: ContentUnit[];
  stageRuns: ETLStageRun[];
  syncRuns: SyncRun[];
  checkpoints: SyncCheckpoint[];

  // Operational Actions
  triggerMasterSync: (stream: MasterStream, full: boolean) => Promise<{ ok: boolean; message: string }>;
  triggerIngestion: (
    resourceId: string,
    bucket: string,
    objectKey: string,
    uploadedBy?: string,
    hashOverride?: string
  ) => Promise<IngestionResult>;
  triggerCreateOcrJob: (versionId: string, triggeredBy?: string) => Promise<{ ok: boolean; job?: OCRJob; message: string }>;
  triggerWorkerOnce: (workerId: string) => Promise<{ ok: boolean; task?: OCRPageTask; message: string }>;
  triggerRecoverStaleTasks: () => Promise<{ ok: boolean; recoveredCount: number; message: string }>;
  triggerPostprocess: (versionId: string) => Promise<{ ok: boolean; message: string; stageRun?: ETLStageRun }>;
  fetchEtlStatus: (versionId: string) => Promise<any>;

  // Theme (default light)
  theme: 'light' | 'dark';
  toggleTheme: () => void;

  // UI Notifications
  notifications: Notification[];
  dismissNotification: (id: string) => void;
}

const EtlContext = createContext<EtlContextType | undefined>(undefined);

export const EtlProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const saved = localStorage.getItem('etl_theme');
    return saved === 'dark' ? 'dark' : 'light';
  });

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  const toggleTheme = () => {
    setTheme((prev) => {
      const next = prev === 'light' ? 'dark' : 'light';
      localStorage.setItem('etl_theme', next);
      return next;
    });
  };

  const [activeScreen, setActiveScreenState] = useState<ScreenId>('overview');
  const [selectedResourceId, setSelectedResourceId] = useState<string | null>('resource-1');
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>('ver-01JJ8A01A01B01C01D01E00001');
  const [selectedJobId, setSelectedJobId] = useState<string | null>('job-01JJ8J01A01B01C01D01E00001');

  // Backend live mode settings
  const [isLiveMode, setIsLiveMode] = useState<boolean>(false);
  const [apiBaseUrl, setApiBaseUrlState] = useState<string>('http://127.0.0.1:8000');
  const [backendStatus, setBackendStatus] = useState<'checking' | 'connected' | 'disconnected' | 'admin_disabled' | 'db_error'>('checking');
  const [lastCorrelationId, setLastCorrelationId] = useState<string | null>(null);
  const [health, setHealth] = useState<SystemHealth>(INITIAL_HEALTH);

  // In-memory operational datasets (Realistic domain models)
  const [resources] = useState<MasterLearningResource[]>(INITIAL_RESOURCES);
  const [versions, setVersions] = useState<ResourceVersion[]>(INITIAL_VERSIONS);
  const [ocrJobs, setOcrJobs] = useState<OCRJob[]>(INITIAL_JOBS);
  const [pageTasks, setPageTasks] = useState<OCRPageTask[]>(INITIAL_PAGE_TASKS);
  const [pageResults, setPageResults] = useState<OCRPageResult[]>(INITIAL_PAGE_RESULTS);
  const [contentUnits, setContentUnits] = useState<ContentUnit[]>(INITIAL_CONTENT_UNITS);
  const [stageRuns, setStageRuns] = useState<ETLStageRun[]>(INITIAL_STAGE_RUNS);
  const [syncRuns, setSyncRuns] = useState<SyncRun[]>(INITIAL_SYNC_RUNS);
  const [checkpoints, setCheckpoints] = useState<SyncCheckpoint[]>(INITIAL_CHECKPOINTS);

  const [notifications, setNotifications] = useState<Notification[]>([]);

  const addNotification = (type: Notification['type'], title: string, message: string) => {
    const id = `notif-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
    setNotifications((prev) => [{ id, type, title, message, timestamp: new Date().toLocaleTimeString() }, ...prev.slice(0, 4)]);
  };

  const dismissNotification = (id: string) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  };

  const setActiveScreen = (
    screen: ScreenId,
    params?: { resourceId?: string; versionId?: string; jobId?: string }
  ) => {
    setActiveScreenState(screen);
    if (params?.resourceId) setSelectedResourceId(params.resourceId);
    if (params?.versionId) setSelectedVersionId(params.versionId);
    if (params?.jobId) setSelectedJobId(params.jobId);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const setApiBaseUrl = (url: string) => {
    setApiBaseUrlState(url);
    api.setBaseUrl(url);
    checkBackendHealth();
  };

  const checkBackendHealth = async () => {
    setBackendStatus('checking');
    const res = await api.getHealth();
    if (res.correlationId) setLastCorrelationId(res.correlationId);

    if (res.ok) {
      setBackendStatus('connected');
      setHealth((h) => ({
        ...h,
        postgres: { status: 'Connected', detail: 'Connected to PostgreSQL 16 via FastAPI' },
        api: { status: 'Running', url: apiBaseUrl, correlation_id: res.correlationId || null },
      }));
    } else {
      if (res.status === 503) {
        setBackendStatus('db_error');
        setHealth((h) => ({
          ...h,
          postgres: { status: 'Error', detail: 'Database unavailable (503)' },
          api: { status: 'Running', url: apiBaseUrl, correlation_id: res.correlationId || null },
        }));
      } else {
        setBackendStatus('disconnected');
        setHealth((h) => ({
          ...h,
          postgres: { status: 'Error', detail: 'Backend server offline or unreachable' },
          api: { status: 'Disabled', url: apiBaseUrl, correlation_id: null },
        }));
      }
    }
  };

  useEffect(() => {
    checkBackendHealth();
  }, []);

  // Action: Master Sync
  const triggerMasterSync = async (
    stream: MasterStream,
    full: boolean
  ): Promise<{ ok: boolean; message: string }> => {
    const runId = `run-${Date.now().toString(36).toUpperCase()}`;
    const startTime = new Date().toISOString();

    if (isLiveMode) {
      const res = await api.runMasterSync(stream, full);
      if (res.correlationId) setLastCorrelationId(res.correlationId);
      if (!res.ok) {
        addNotification('error', `Sync Thất Bại (HTTP ${res.status})`, res.error || 'Lỗi khi gọi API');
        return { ok: false, message: res.error || 'Failed' };
      }
      addNotification('success', 'Master Sync Thành Công', `Stream: ${stream} (${full ? 'Full' : 'Incremental'})`);
      return { ok: true, message: 'Sync completed via backend API' };
    }

    // Prototype Mode Simulation: Follows domain rules
    // streams order: grades -> subjects -> users -> resources
    let recordsRead = 0;
    let inserted = 0;
    let updated = 0;
    let skipped = 0;

    if (stream === 'all' || stream === 'grades') {
      recordsRead += 3;
      if (full) inserted += 3; else skipped += 3;
    }
    if (stream === 'all' || stream === 'subjects') {
      recordsRead += 3;
      if (full) inserted += 3; else skipped += 3;
    }
    if (stream === 'all' || stream === 'users') {
      recordsRead += 10;
      if (full) inserted += 10; else skipped += 10;
    }
    if (stream === 'all' || stream === 'resources') {
      recordsRead += 3;
      if (full) inserted += 3; else updated += 1; skipped += 2;
    }

    const newRun: SyncRun = {
      id: runId,
      stream_name: stream,
      started_at: startTime,
      finished_at: new Date().toISOString(),
      status: 'COMPLETED',
      records_read: recordsRead,
      inserted,
      updated,
      skipped,
      failed: 0,
      checkpoint_before: {
        last_updated_at: '2026-01-01T00:00:00Z',
        last_id: stream === 'all' ? 'resource-3' : `${stream}-3`,
      },
      checkpoint_after: {
        last_updated_at: new Date().toISOString(),
        last_id: stream === 'all' ? 'resource-3' : `${stream}-3`,
      },
      error_message: null,
    };

    setSyncRuns((prev) => [newRun, ...prev]);

    // Update checkpoints
    const nowIso = new Date().toISOString();
    setCheckpoints((prev) =>
      prev.map((cp) => {
        if (stream === 'all' || cp.stream_name === stream) {
          return {
            ...cp,
            last_updated_at: nowIso,
            updated_at: nowIso,
          };
        }
        return cp;
      })
    );

    addNotification(
      'success',
      'Đồng bộ Master Data thành công',
      `Stream: ${stream.toUpperCase()} | Đã đọc: ${recordsRead} | Thêm: ${inserted} | Cập nhật: ${updated} | Bỏ qua: ${skipped}`
    );

    return { ok: true, message: 'Sync run recorded successfully' };
  };

  // Action: Ingestion (Immutability & SHA-256 deduplication logic)
  const triggerIngestion = async (
    resourceId: string,
    bucket: string,
    objectKey: string,
    uploadedBy?: string,
    hashOverride?: string
  ): Promise<IngestionResult> => {
    if (isLiveMode) {
      const res = await api.ingestResource(resourceId, bucket, objectKey, uploadedBy);
      if (res.correlationId) setLastCorrelationId(res.correlationId);
      if (!res.ok) {
        addNotification('error', `Ingest Thất Bại (HTTP ${res.status})`, res.error || 'Lỗi API');
        throw new Error(res.error);
      }
      return res.data as IngestionResult;
    }

    // Prototype Mode: Faithful domain logic
    const resource = resources.find((r) => r.id === resourceId);
    if (!resource) {
      const msg = `Resource '${resourceId}' không tồn tại trong danh mục master.`;
      addNotification('error', 'Lỗi Ingestion', msg);
      throw new Error(msg);
    }
    if (!resource.is_active) {
      const msg = `Resource '${resourceId}' đang ở trạng thái không hoạt động (is_active = false).`;
      addNotification('error', 'Lỗi Ingestion', msg);
      throw new Error(msg);
    }

    // Find latest version of this resource
    const resourceVersions = versions
      .filter((v) => v.resource_id === resourceId)
      .sort((a, b) => b.version_number - a.version_number);
    const latestVersion = resourceVersions[0] || null;

    // Computed or simulated hash
    const targetHash =
      hashOverride && hashOverride.trim()
        ? hashOverride.trim()
        : `${Math.random().toString(16).substring(2)}${Math.random().toString(16).substring(2)}4a9e5272a8019b`;

    // Rule: Same hash as latest version -> NO_CHANGE
    if (latestVersion && latestVersion.source_hash === targetHash) {
      const result: IngestionResult = {
        action: 'NO_CHANGE',
        version: latestVersion,
        reason: 'Nội dung file có cùng SHA-256 hash với phiên bản mới nhất hiện tại. Bỏ qua tạo version mới.',
      };
      addNotification('info', 'Ingest: NO_CHANGE', `File trùng hash (${targetHash.substring(0, 12)}...) với Version ${latestVersion.version_number}.`);
      return result;
    }

    // Rule: Different hash -> Create new version v(N+1)
    const newVersionNumber = latestVersion ? latestVersion.version_number + 1 : 1;
    const newVersionId = `ver-${Date.now().toString(36).toUpperCase()}${Math.random().toString(36).substring(2, 6).toUpperCase()}`;
    const newVersion: ResourceVersion = {
      id: newVersionId,
      resource_id: resourceId,
      parent_version_id: latestVersion ? latestVersion.id : null,
      version_number: newVersionNumber,
      lifecycle_status: 'INGESTED',
      storage_bucket: bucket,
      storage_object_key: objectKey,
      source_hash: targetHash,
      uploaded_by: uploadedBy || 'operator-web',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    setVersions((prev) => [newVersion, ...prev]);
    setSelectedVersionId(newVersionId);

    const result: IngestionResult = {
      action: 'CREATED',
      version: newVersion,
    };

    addNotification(
      'success',
      `Tạo Version ${newVersionNumber} Thành Công`,
      `Resource: ${resourceId} | Hash: ${targetHash.substring(0, 16)}... | Bucket: ${bucket}/${objectKey}`
    );

    return result;
  };

  // Action: Create OCR Job (Idempotent reuse)
  const triggerCreateOcrJob = async (
    versionId: string,
    triggeredBy?: string
  ): Promise<{ ok: boolean; job?: OCRJob; message: string }> => {
    if (isLiveMode) {
      const res = await api.createOcrJob(versionId, triggeredBy);
      if (res.correlationId) setLastCorrelationId(res.correlationId);
      if (!res.ok) {
        addNotification('error', `Tạo OCR Job Thất Bại (HTTP ${res.status})`, res.error || 'Lỗi');
        return { ok: false, message: res.error || 'Failed' };
      }
      return { ok: true, job: res.data, message: 'Job created via backend' };
    }

    // Prototype Mode: Idempotent - return existing job if present
    const existingJob = ocrJobs.find((j) => j.resource_version_id === versionId);
    if (existingJob) {
      addNotification('info', 'Tái sử dụng OCR Job', `Đã tồn tại OCR Job ${existingJob.id} cho Version này (Idempotent).`);
      setSelectedJobId(existingJob.id);
      return { ok: true, job: existingJob, message: 'Reused existing job' };
    }

    const totalPages = Math.floor(Math.random() * 3) + 3; // 3 to 5 pages
    const jobId = `job-${Date.now().toString(36).toUpperCase()}`;

    const newJob: OCRJob = {
      id: jobId,
      resource_version_id: versionId,
      triggered_by: triggeredBy || 'web-operator',
      status: 'PENDING',
      total_pages: totalPages,
      completed_pages: 0,
      failed_pages: 0,
      started_at: null,
      completed_at: null,
      postprocess_enqueued_at: null,
    };

    // Create page tasks
    const newTasks: OCRPageTask[] = [];
    for (let p = 1; p <= totalPages; p++) {
      newTasks.push({
        id: `task-${jobId}-p${p}`,
        job_id: jobId,
        page_num: p,
        local_image_path: null,
        status: 'PENDING',
        worker_id: null,
        retry_count: 0,
        error_message: null,
        heartbeat_at: null,
        started_at: null,
        completed_at: null,
        claim_token: null,
      });
    }

    setOcrJobs((prev) => [newJob, ...prev]);
    setPageTasks((prev) => [...newTasks, ...prev]);
    setSelectedJobId(jobId);

    addNotification(
      'success',
      'Đã khởi tạo OCR Job',
      `Job ID: ${jobId} | Tổng số trang: ${totalPages} | Trạng thái: PENDING`
    );

    return { ok: true, job: newJob, message: 'New OCR job created' };
  };

  // Action: Worker step (Run worker once: claim, process, succeed)
  const triggerWorkerOnce = async (
    workerId: string
  ): Promise<{ ok: boolean; task?: OCRPageTask; message: string }> => {
    // Find next pending task
    const pendingTaskIndex = pageTasks.findIndex((t) => t.status === 'PENDING');
    if (pendingTaskIndex === -1) {
      addNotification('info', 'Worker Thông Báo', `Không có OCR task nào ở trạng thái PENDING.`);
      return { ok: false, message: 'No pending tasks to process' };
    }

    const task = pageTasks[pendingTaskIndex];
    const job = ocrJobs.find((j) => j.id === task.job_id);
    const nowIso = new Date().toISOString();

    // Succeeded result simulation (Native PDF Text)
    const resultId = `res-${task.id}`;
    const sampleText = `Trang ${task.page_num}: Nội dung văn bản giáo khoa trích xuất từ native PDF layer.\nĐơn vị kiến thức chuẩn bài học tiểu học.`;

    const newResult: OCRPageResult = {
      id: resultId,
      page_task_id: task.id,
      raw_text: sampleText,
      normalized_text: sampleText,
      confidence: 1.0,
      engine_name: 'native_pdf_text',
      engine_version: 'PyMuPDF-1.24.1',
      result_metadata: { page_num: task.page_num },
      created_at: nowIso,
      updated_at: nowIso,
    };

    const updatedTask: OCRPageTask = {
      ...task,
      status: 'SUCCEEDED',
      worker_id: workerId,
      claim_token: null,
      started_at: nowIso,
      completed_at: nowIso,
      heartbeat_at: nowIso,
    };

    setPageTasks((prev) => {
      const copy = [...prev];
      copy[pendingTaskIndex] = updatedTask;
      return copy;
    });

    setPageResults((prev) => [newResult, ...prev]);

    // Update job progress
    if (job) {
      const newCompleted = job.completed_pages + 1;
      const isTerminal = newCompleted + job.failed_pages >= job.total_pages;
      const newJobStatus = isTerminal
        ? job.failed_pages > 0
          ? 'COMPLETED_WITH_ERRORS'
          : 'COMPLETED'
        : 'RUNNING';

      setOcrJobs((prev) =>
        prev.map((j) =>
          j.id === job.id
            ? {
                ...j,
                status: newJobStatus,
                completed_pages: newCompleted,
                started_at: j.started_at || nowIso,
                completed_at: isTerminal ? nowIso : null,
              }
            : j
        )
      );
    }

    addNotification(
      'success',
      `Worker [${workerId}] xử lý hoàn tất`,
      `Trang ${task.page_num} (Job ${task.job_id}) -> SUCCEEDED (engine: native_pdf_text)`
    );

    return { ok: true, task: updatedTask, message: 'Processed one task successfully' };
  };

  // Action: Recover stale tasks
  const triggerRecoverStaleTasks = async (): Promise<{
    ok: boolean;
    recoveredCount: number;
    message: string;
  }> => {
    // Detect tasks that are RUNNING but heartbeat has expired (> 120s)
    let count = 0;
    setPageTasks((prev) =>
      prev.map((t) => {
        if (t.status === 'RUNNING') {
          count++;
          const newRetries = t.retry_count + 1;
          const terminal = newRetries >= 3;
          return {
            ...t,
            status: terminal ? 'FAILED' : 'PENDING',
            retry_count: newRetries,
            error_message: terminal
              ? 'Worker lease expired; reached max retries (3)'
              : 'Worker lease expired; re-queued to PENDING',
            worker_id: null,
            claim_token: null,
          };
        }
        return t;
      })
    );

    const msg = count > 0 ? `Đã khôi phục ${count} task quá hạn lease.` : 'Không có stale task nào cần khôi phục.';
    addNotification('info', 'Recover Stale Tasks', msg);
    return { ok: true, recoveredCount: count, message: msg };
  };

  // Action: Postprocess
  const triggerPostprocess = async (
    versionId: string
  ): Promise<{ ok: boolean; message: string; stageRun?: ETLStageRun }> => {
    if (isLiveMode) {
      const res = await api.runPostprocess(versionId);
      if (res.correlationId) setLastCorrelationId(res.correlationId);
      if (!res.ok) {
        addNotification('error', `Postprocess Thất Bại (HTTP ${res.status})`, res.error || 'Lỗi');
        return { ok: false, message: res.error || 'Failed' };
      }
      return { ok: true, message: 'Postprocess finished on backend' };
    }

    // Prototype Mode
    const latestJob = ocrJobs.find((j) => j.resource_version_id === versionId);
    if (!latestJob) {
      const msg = 'Chưa có OCR Job nào cho phiên bản tài nguyên này.';
      addNotification('error', 'Lỗi Hậu Xử Lý', msg);
      return { ok: false, message: msg };
    }

    if (latestJob.status !== 'COMPLETED' && latestJob.status !== 'COMPLETED_WITH_ERRORS') {
      const msg = `Hậu xử lý yêu cầu OCR Job ở trạng thái hoàn thành (COMPLETED/COMPLETED_WITH_ERRORS). Hiện tại: ${latestJob.status}`;
      addNotification('warning', 'Chưa Đạt Điều Kiện Postprocess', msg);
      return { ok: false, message: msg };
    }

    // Collect succeeded results
    const jobTasks = pageTasks.filter((t) => t.job_id === latestJob.id && t.status === 'SUCCEEDED');
    const stageId = `stg-${Date.now().toString(36).toUpperCase()}`;
    const nowIso = new Date().toISOString();

    const createdUnits: ContentUnit[] = [];
    jobTasks.forEach((t, idx) => {
      const res = pageResults.find((r) => r.page_task_id === t.id);
      if (res && res.normalized_text?.trim()) {
        createdUnits.push({
          id: `cu-${stageId}-${idx + 1}`,
          resource_version_id: versionId,
          parent_unit_id: null,
          unit_type: 'PAGE_TEXT',
          title: null,
          sequence_no: idx + 1,
          page_from: t.page_num,
          page_to: t.page_num,
          review_status: 'UNREVIEWED',
          retrieval_eligible: false,
          text: res.normalized_text,
          page_result_id: res.id,
          created_at: nowIso,
          updated_at: nowIso,
        });
      }
    });

    // Replace units for this version
    setContentUnits((prev) => [
      ...createdUnits,
      ...prev.filter((u) => u.resource_version_id !== versionId),
    ]);

    const newStageRun: ETLStageRun = {
      id: stageId,
      resource_version_id: versionId,
      stage: 'POSTPROCESS',
      status: 'COMPLETED',
      started_at: nowIso,
      finished_at: new Date(Date.now() + 1200).toISOString(),
      input_fingerprint: `nfc-${Math.random().toString(16).substring(2)}${Math.random().toString(16).substring(2)}`,
      error_message: null,
      coverage: {
        successful_pages: jobTasks.length,
        total_pages: latestJob.total_pages,
        failed_pages: latestJob.failed_pages,
      },
    };

    setStageRuns((prev) => [newStageRun, ...prev]);

    addNotification(
      'success',
      'Hậu Xử Lý Hoàn Tất (Postprocess)',
      `Đã tạo ${createdUnits.length} Content Units (NFC normalized) | Độ phủ: ${jobTasks.length}/${latestJob.total_pages} trang`
    );

    return { ok: true, message: 'Postprocess finished', stageRun: newStageRun };
  };

  // Inspect status
  const fetchEtlStatus = async (versionId: string) => {
    if (isLiveMode) {
      const res = await api.getEtlStatus(versionId);
      if (res.correlationId) setLastCorrelationId(res.correlationId);
      return res.data;
    }
    const version = versions.find((v) => v.id === versionId);
    if (!version) return null;
    const resource = resources.find((r) => r.id === version.resource_id);
    const job = ocrJobs.find((j) => j.resource_version_id === versionId) || null;
    const stages = stageRuns.filter((s) => s.resource_version_id === versionId);
    const count = contentUnits.filter((u) => u.resource_version_id === versionId).length;

    return {
      resource,
      version,
      latest_version: version,
      ocr_job: job,
      stage_runs: stages,
      content_unit_count: count,
    };
  };

  return (
    <EtlContext.Provider
      value={{
        activeScreen,
        setActiveScreen,
        selectedResourceId,
        setSelectedResourceId,
        selectedVersionId,
        setSelectedVersionId,
        selectedJobId,
        setSelectedJobId,
        isLiveMode,
        setIsLiveMode,
        apiBaseUrl,
        setApiBaseUrl,
        backendStatus,
        lastCorrelationId,
        health,
        checkBackendHealth,
        resources,
        versions,
        ocrJobs,
        pageTasks,
        pageResults,
        contentUnits,
        stageRuns,
        syncRuns,
        checkpoints,
        triggerMasterSync,
        triggerIngestion,
        triggerCreateOcrJob,
        triggerWorkerOnce,
        triggerRecoverStaleTasks,
        triggerPostprocess,
        fetchEtlStatus,
        theme,
        toggleTheme,
        notifications,
        dismissNotification,
      }}
    >
      {children}
    </EtlContext.Provider>
  );
};

export const useEtl = () => {
  const ctx = useContext(EtlContext);
  if (!ctx) throw new Error('useEtl must be used within EtlProvider');
  return ctx;
};
