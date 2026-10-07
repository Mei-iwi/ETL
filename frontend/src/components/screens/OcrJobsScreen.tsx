import React, { useState } from 'react';
import { useEtl } from '../../context/EtlContext';
import type { OCRPageTask } from '../../types/etl';
import { StatusBadge } from '../common/StatusBadge';
import { ProgressBar } from '../common/ProgressBar';
import { Drawer } from '../common/Drawer';
import {
  Warning,
  Eye,
  ArrowRight,
  Cpu,
} from '@phosphor-icons/react';

export const OcrJobsScreen: React.FC = () => {
  const {
    ocrJobs,
    pageTasks,
    pageResults,
    selectedJobId,
    setSelectedJobId,
    setActiveScreen,
  } = useEtl();

  const [activeJobId, setActiveJobId] = useState<string>(
    selectedJobId || (ocrJobs[0] ? ocrJobs[0].id : '')
  );
  const [selectedTask, setSelectedTask] = useState<OCRPageTask | null>(null);

  const currentJob = ocrJobs.find((j) => j.id === activeJobId) || ocrJobs[0] || null;
  const currentJobTasks = currentJob
    ? pageTasks.filter((t) => t.job_id === currentJob.id).sort((a, b) => a.page_num - b.page_num)
    : [];

  const taskResult = selectedTask
    ? pageResults.find((r) => r.page_task_id === selectedTask.id) || null
    : null;

  return (
    <div>
      <div className="page-header">
        <div className="page-title-row">
          <div>
            <h1 className="page-title">OCR Jobs & Page Tasks</h1>
            <p className="page-description">
              Extract document page text via PyMuPDF native extractor or OCR engine. Per-page distributed worker tasks with heartbeat tracking and up to 3 retries.
            </p>
          </div>
          <div className="page-actions">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setActiveScreen('operations')}
            >
              <Cpu size={15} /> Workers & Recovery
            </button>
          </div>
        </div>
      </div>

      {/* OCR Jobs Table */}
      <div className="card" style={{ marginBottom: '20px' }}>
        <div className="card-header">
          <div>
            <div className="card-title">OCR Jobs (ocr_jobs)</div>
            <div className="card-subtitle">Select a job to inspect decomposed per-page tasks</div>
          </div>
          <span className="hash-pill">{ocrJobs.length} jobs</span>
        </div>

        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: '150px' }}>Job ID</th>
                <th>Resource Version</th>
                <th style={{ width: '120px' }}>Status</th>
                <th style={{ width: '180px' }}>Progress</th>
                <th style={{ width: '130px' }}>Pages (Done/Fail/Tot)</th>
                <th style={{ width: '140px' }}>Started</th>
                <th style={{ width: '140px' }}>Finished</th>
                <th style={{ width: '120px', textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {ocrJobs.map((job) => {
                const isSelected = currentJob?.id === job.id;
                return (
                  <tr
                    key={job.id}
                    onClick={() => {
                      setActiveJobId(job.id);
                      setSelectedJobId(job.id);
                    }}
                    style={{
                      cursor: 'pointer',
                      background: isSelected ? 'var(--bg-hover)' : undefined,
                    }}
                  >
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <span className="mono" style={{ fontWeight: 600, fontSize: '12px' }}>
                          {job.id.substring(0, 14)}...
                        </span>
                        {isSelected && (
                          <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'var(--accent-primary)' }} />
                        )}
                      </div>
                    </td>
                    <td>
                      <span className="mono" style={{ fontSize: '12px' }}>
                        {job.resource_version_id}
                      </span>
                    </td>
                    <td>
                      <StatusBadge status={job.status} />
                    </td>
                    <td>
                      <ProgressBar
                        completed={job.completed_pages}
                        failed={job.failed_pages}
                        total={job.total_pages}
                      />
                    </td>
                    <td>
                      <span className="mono" style={{ fontSize: '12px' }}>
                        {job.completed_pages} / {job.failed_pages} / {job.total_pages}
                      </span>
                    </td>
                    <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                      {job.started_at ? new Date(job.started_at).toLocaleTimeString('vi-VN') : '—'}
                    </td>
                    <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                      {job.completed_at ? new Date(job.completed_at).toLocaleTimeString('vi-VN') : '—'}
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <button
                        type="button"
                        className={`btn ${isSelected ? 'btn-primary' : 'btn-ghost'} btn-sm`}
                        onClick={(e) => {
                          e.stopPropagation();
                          setActiveJobId(job.id);
                          setSelectedJobId(job.id);
                        }}
                      >
                        {isSelected ? 'Viewing' : 'Select'}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Selected Job's Page Tasks Monitoring */}
      {currentJob ? (
        <div className="card">
          <div className="card-header">
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span className="card-title">Page Tasks for Job</span>
                <span className="mono" style={{ fontSize: '13px', fontWeight: 600 }}>{currentJob.id}</span>
                <StatusBadge status={currentJob.status} />
              </div>
              <div className="card-subtitle">
                Version: <span className="mono">{currentJob.resource_version_id}</span> · Triggered by: {currentJob.triggered_by || 'system'}
              </div>
            </div>

            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              {(currentJob.status === 'COMPLETED' || currentJob.status === 'COMPLETED_WITH_ERRORS') && (
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={() => {
                    setActiveScreen('postprocess', { versionId: currentJob.resource_version_id });
                  }}
                >
                  Proceed to Postprocess <ArrowRight size={12} />
                </button>
              )}
              <span className="hash-pill">{currentJobTasks.length} tasks</span>
            </div>
          </div>

          <div className="table-container">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: '70px' }}>Page</th>
                  <th>Task ID</th>
                  <th style={{ width: '120px' }}>Status</th>
                  <th style={{ width: '90px' }}>Retries</th>
                  <th>Worker ID</th>
                  <th style={{ width: '130px' }}>Started</th>
                  <th style={{ width: '130px' }}>Completed</th>
                  <th style={{ width: '100px', textAlign: 'right' }}>Inspection</th>
                </tr>
              </thead>
              <tbody>
                {currentJobTasks.map((task) => (
                  <tr key={task.id}>
                    <td>
                      <span className="mono" style={{ fontWeight: 600 }}>P.{task.page_num}</span>
                    </td>
                    <td>
                      <span className="mono" style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                        {task.id}
                      </span>
                    </td>
                    <td>
                      <StatusBadge status={task.status} />
                    </td>
                    <td>
                      <span className="mono" style={{ fontSize: '12px' }}>
                        {task.retry_count} / 3
                      </span>
                    </td>
                    <td>
                      <span className="mono" style={{ fontSize: '12px' }}>
                        {task.worker_id || '—'}
                      </span>
                    </td>
                    <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                      {task.started_at ? new Date(task.started_at).toLocaleTimeString('vi-VN') : '—'}
                    </td>
                    <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                      {task.completed_at ? new Date(task.completed_at).toLocaleTimeString('vi-VN') : '—'}
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => setSelectedTask(task)}
                      >
                        <Eye size={12} /> Text
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="card" style={{ textAlign: 'center', padding: '36px', color: 'var(--text-muted)' }}>
          No OCR Job selected.
        </div>
      )}

      {/* Drawer: Page Task Inspection */}
      <Drawer
        isOpen={!!selectedTask}
        onClose={() => setSelectedTask(null)}
        title={selectedTask ? `Page Task Inspection — Page ${selectedTask.page_num}` : ''}
        subtitle={selectedTask ? `Task ID: ${selectedTask.id}` : undefined}
      >
        {selectedTask && (
          <div>
            <div className="kv-grid" style={{ marginBottom: '18px' }}>
              <span className="kv-key">Page Number:</span>
              <span className="kv-val mono" style={{ fontWeight: 600 }}>Page {selectedTask.page_num}</span>

              <span className="kv-key">Task Status:</span>
              <span className="kv-val">
                <StatusBadge status={selectedTask.status} />
              </span>

              <span className="kv-key">Retry Attempts:</span>
              <span className="kv-val mono">{selectedTask.retry_count} / 3</span>

              <span className="kv-key">Worker Assigned:</span>
              <span className="kv-val mono">{selectedTask.worker_id || 'None'}</span>

              <span className="kv-key">Started:</span>
              <span className="kv-val">{selectedTask.started_at || 'Not started'}</span>

              <span className="kv-key">Completed:</span>
              <span className="kv-val">{selectedTask.completed_at || 'Not finished'}</span>

              <span className="kv-key">Heartbeat:</span>
              <span className="kv-val">{selectedTask.heartbeat_at || 'None'}</span>
            </div>

            {selectedTask.error_message && (
              <div className="notice-box warning" style={{ marginBottom: '18px' }}>
                <Warning size={16} color="var(--status-error)" style={{ flexShrink: 0 }} />
                <div>
                  <div className="notice-title">Error Details</div>
                  <div className="notice-body">{selectedTask.error_message}</div>
                </div>
              </div>
            )}

            {/* OCR Page Result */}
            <div style={{ marginTop: '16px' }}>
              <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px', color: 'var(--text-primary)' }}>
                OCR Page Output (ocr_page_results)
              </div>

              {!taskResult ? (
                <div
                  style={{
                    padding: '20px',
                    textAlign: 'center',
                    background: 'var(--bg-app)',
                    border: '1px solid var(--border-default)',
                    borderRadius: 'var(--radius-sm)',
                    color: 'var(--text-muted)',
                    fontSize: '12px',
                  }}
                >
                  No OCR result recorded yet for this task (Current status: {selectedTask.status}).
                </div>
              ) : (
                <div>
                  <div className="kv-grid" style={{ marginBottom: '14px' }}>
                    <span className="kv-key">Engine:</span>
                    <span className="kv-val mono">{taskResult.engine_name}</span>

                    <span className="kv-key">Engine Version:</span>
                    <span className="kv-val mono">{taskResult.engine_version || 'N/A'}</span>

                    <span className="kv-key">Confidence:</span>
                    <span className="kv-val mono">
                      {taskResult.confidence !== null ? `${(taskResult.confidence * 100).toFixed(1)}%` : 'N/A'}
                    </span>
                  </div>

                  <div className="form-group">
                    <label className="form-label">
                      <span>Raw Extracted Text</span>
                    </label>
                    <textarea
                      className="form-textarea mono"
                      style={{ width: '100%', height: '120px', fontSize: '12px' }}
                      readOnly
                      value={taskResult.raw_text}
                    />
                  </div>

                  <div className="form-group" style={{ marginTop: '12px' }}>
                    <label className="form-label">
                      <span>Normalized Text (Post-processing)</span>
                    </label>
                    <textarea
                      className="form-textarea mono"
                      style={{ width: '100%', height: '120px', fontSize: '12px' }}
                      readOnly
                      value={taskResult.normalized_text || 'Pending postprocess execution'}
                    />
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
};
