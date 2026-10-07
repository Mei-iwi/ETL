import React, { useState } from 'react';
import { useEtl } from '../../context/EtlContext';
import { StatusBadge } from '../common/StatusBadge';
import {
  Play,
  ShieldWarning,
  Terminal,
} from '@phosphor-icons/react';

export const OperationsScreen: React.FC = () => {
  const {
    isLiveMode,
    pageTasks,
    triggerWorkerOnce,
    triggerRecoverStaleTasks,
  } = useEtl();

  const [workerId, setWorkerId] = useState('worker-1');
  const [isProcessing, setIsProcessing] = useState(false);
  const [isRecovering, setIsRecovering] = useState(false);

  const runningTasks = pageTasks.filter((t) => t.status === 'RUNNING');
  const pendingTasks = pageTasks.filter((t) => t.status === 'PENDING');
  const failedTasks = pageTasks.filter((t) => t.status === 'FAILED');

  const handleRunWorkerOnce = async () => {
    setIsProcessing(true);
    try {
      await triggerWorkerOnce(workerId);
    } finally {
      setIsProcessing(false);
    }
  };

  const handleRecoverStale = async () => {
    setIsRecovering(true);
    try {
      await triggerRecoverStaleTasks();
    } finally {
      setIsRecovering(false);
    }
  };

  return (
    <div>
      <div className="page-header">
        <div className="page-title-row">
          <div>
            <h1 className="page-title">Workers & Recovery</h1>
            <p className="page-description">
              OCR worker execution lifecycle, periodic heartbeat monitoring (120s lease window), and stale task recovery.
            </p>
          </div>
          <div className="page-actions">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleRecoverStale}
              disabled={isRecovering || isLiveMode}
            >
              <ShieldWarning size={15} />
              {isRecovering ? 'Recovering...' : 'Recover Stale Tasks'}
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleRunWorkerOnce}
              disabled={isLiveMode || isProcessing || pendingTasks.length === 0}
            >
              <Play size={15} />
              {isProcessing ? 'Processing...' : `Run Worker Once (${pendingTasks.length} queued)`}
            </button>
          </div>
        </div>
      </div>

      {isLiveMode && <p className="notice-box">Worker và recovery chưa có API. Dùng các lệnh CLI bên dưới; các nút mô phỏng đã tắt.</p>}
      {/* CLI Daemon Reference */}
      <div
        style={{
          background: 'var(--bg-app)',
          border: '1px solid var(--border-default)',
          borderRadius: 'var(--radius-sm)',
          padding: '12px 16px',
          marginBottom: '20px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '16px',
          fontSize: '12.5px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Terminal size={16} color="var(--text-secondary)" />
          <span style={{ color: 'var(--text-secondary)' }}>Production CLI Daemons:</span>
          <code className="mono">etl run-ocr-worker --worker-id &lt;id&gt;</code>
          <span style={{ color: 'var(--text-muted)' }}>·</span>
          <code className="mono">etl recover-stale-ocr-tasks</code>
        </div>
        <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          Workers run as independent OS processes with <code>SELECT FOR UPDATE SKIP LOCKED</code>.
        </div>
      </div>

      {/* Operator Control Bar */}
      <div className="card" style={{ marginBottom: '20px' }}>
        <div className="card-header">
          <div>
            <div className="card-title">Worker Dispatch Console</div>
            <div className="card-subtitle">Trigger single worker cycles or reclaim hung lease locks</div>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <label style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)' }}>Worker ID:</label>
            <input
              type="text"
              className="form-input mono"
              value={workerId}
              onChange={(e) => setWorkerId(e.target.value)}
              style={{ width: '140px', height: '34px' }}
              placeholder="worker-1"
            />
          </div>

          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={handleRunWorkerOnce}
            disabled={isLiveMode || isProcessing || pendingTasks.length === 0}
          >
            <Play size={13} />
            {isProcessing ? 'Executing...' : `Claim & Process 1 Task (${pendingTasks.length} Pending)`}
          </button>

          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handleRecoverStale}
            disabled={isRecovering || isLiveMode}
          >
            <ShieldWarning size={13} />
            Scan & Recover Stale Leases
          </button>
        </div>
      </div>

      {/* Grid: Running Tasks Table & Failed Tasks Table */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 1fr', gap: '20px' }}>
        {/* Active Running Tasks */}
        <div className="card">
          <div className="card-header">
            <div>
              <div className="card-title">Active Running Tasks</div>
              <div className="card-subtitle">Heartbeat monitored within 120-second lease window</div>
            </div>
            <span className="hash-pill">{runningTasks.length} running</span>
          </div>

          {runningTasks.length === 0 ? (
            <div style={{ padding: '36px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
              No worker tasks currently in RUNNING state.
            </div>
          ) : (
            <div className="table-container">
              <table className="table">
                <thead>
                  <tr>
                    <th>Task ID</th>
                    <th style={{ width: '70px' }}>Page</th>
                    <th>Worker ID</th>
                    <th>Heartbeat</th>
                    <th style={{ width: '90px' }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {runningTasks.map((t) => (
                    <tr key={t.id}>
                      <td>
                        <span className="mono" style={{ fontSize: '11px', fontWeight: 600 }}>
                          {t.id.substring(0, 14)}...
                        </span>
                      </td>
                      <td>
                        <span className="mono" style={{ fontWeight: 600 }}>P.{t.page_num}</span>
                      </td>
                      <td>
                        <span className="mono" style={{ fontSize: '12px' }}>{t.worker_id}</span>
                      </td>
                      <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                        {t.heartbeat_at ? new Date(t.heartbeat_at).toLocaleTimeString('vi-VN') : 'Active'}
                      </td>
                      <td>
                        <StatusBadge status="RUNNING" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Failed / Stale Tasks */}
        <div className="card">
          <div className="card-header">
            <div>
              <div className="card-title">Failed Tasks (Retries Exhausted)</div>
              <div className="card-subtitle">Tasks reaching maximum retries (OCR_MAX_RETRIES=3)</div>
            </div>
            <span className="hash-pill">{failedTasks.length} failed</span>
          </div>

          {failedTasks.length === 0 ? (
            <div style={{ padding: '36px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
              No tasks in FAILED state. All processed pages are healthy.
            </div>
          ) : (
            <div className="table-container">
              <table className="table">
                <thead>
                  <tr>
                    <th>Task ID</th>
                    <th style={{ width: '60px' }}>Page</th>
                    <th style={{ width: '60px' }}>Retry</th>
                    <th>Error Detail</th>
                    <th style={{ width: '80px' }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {failedTasks.map((t) => (
                    <tr key={t.id}>
                      <td>
                        <span className="mono" style={{ fontSize: '11px' }}>
                          {t.id.substring(0, 12)}...
                        </span>
                      </td>
                      <td>
                        <span className="mono">P.{t.page_num}</span>
                      </td>
                      <td>
                        <span className="mono">{t.retry_count}/3</span>
                      </td>
                      <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                        {t.error_message || 'Maximum retries exceeded'}
                      </td>
                      <td>
                        <StatusBadge status="FAILED" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
