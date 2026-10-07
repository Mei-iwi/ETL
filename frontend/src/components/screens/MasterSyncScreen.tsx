import React, { useState } from 'react';
import { useEtl } from '../../context/EtlContext';
import type { MasterStream, SyncRun } from '../../types/etl';
import { StatusBadge } from '../common/StatusBadge';
import { Drawer } from '../common/Drawer';
import { Play } from '@phosphor-icons/react';

export const MasterSyncScreen: React.FC = () => {
  const {
    syncRuns,
    checkpoints,
    triggerMasterSync,
  } = useEtl();

  const [selectedStream, setSelectedStream] = useState<MasterStream>('all');
  const [isFullSync, setIsFullSync] = useState<boolean>(false);
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [viewingRun, setViewingRun] = useState<SyncRun | null>(null);

  const handleRunSync = async () => {
    setIsRunning(true);
    try {
      await triggerMasterSync(selectedStream, isFullSync);
    } finally {
      setIsRunning(false);
    }
  };

  return (
    <div>
      {/* Header */}
      <div className="page-header">
        <div className="page-title-row">
          <div>
            <h1 className="page-title">Master Data Sync</h1>
            <p className="page-description">
              Synchronize master data snapshots into PostgreSQL.
            </p>
          </div>
        </div>
      </div>

      {/* Sync Controls (Simple clean panel, NOT oversized card) */}
      <div className="section-panel">
        <div className="section-panel-body" style={{ padding: '14px 16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '24px', flexWrap: 'wrap' }}>
            {/* Stream */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <label style={{ fontSize: '12.5px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                Stream:
              </label>
              <select
                className="form-select mono"
                style={{ height: '32px', padding: '0 10px', fontSize: '12px', minWidth: '150px' }}
                value={selectedStream}
                onChange={(e) => setSelectedStream(e.target.value as MasterStream)}
                disabled={isRunning}
              >
                <option value="all">All Streams</option>
                <option value="grades">Grades</option>
                <option value="subjects">Subjects</option>
                <option value="users">Users</option>
                <option value="resources">Resources</option>
              </select>
            </div>

            {/* Mode Radios */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
              <span style={{ fontSize: '12.5px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                Mode:
              </span>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '12.5px', cursor: 'pointer' }}>
                <input
                  type="radio"
                  name="syncMode"
                  checked={!isFullSync}
                  onChange={() => setIsFullSync(false)}
                  disabled={isRunning}
                />
                Incremental (Checkpoint)
              </label>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '12.5px', cursor: 'pointer' }}>
                <input
                  type="radio"
                  name="syncMode"
                  checked={isFullSync}
                  onChange={() => setIsFullSync(true)}
                  disabled={isRunning}
                />
                Full (All records)
              </label>
            </div>

            {/* Run Button */}
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={handleRunSync}
              disabled={isRunning}
              style={{ marginLeft: 'auto' }}
            >
              <Play size={12} weight="bold" />
              {isRunning ? 'Running...' : 'Run Sync'}
            </button>
          </div>
        </div>
      </div>

      {/* Checkpoints Table (As requested: table instead of 4 cards!) */}
      <div className="section-panel">
        <div className="section-panel-header">
          <div className="section-panel-title">Checkpoints (sync_checkpoints)</div>
          <span className="hash-pill">4 streams</span>
        </div>

        <div className="table-wrapper" style={{ margin: 0, border: 'none', borderRadius: 0 }}>
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: '160px' }}>Stream</th>
                <th style={{ width: '180px' }}>Last Record</th>
                <th>Watermark</th>
                <th style={{ width: '160px' }}>Last Sync</th>
                <th style={{ width: '120px' }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {checkpoints.map((cp) => (
                <tr key={cp.stream_name}>
                  <td style={{ fontWeight: 600 }}>
                    {cp.stream_name.charAt(0).toUpperCase() + cp.stream_name.slice(1)}
                  </td>
                  <td>
                    <span className="mono">{cp.last_id || 'NULL'}</span>
                  </td>
                  <td>
                    <span className="mono" style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>
                      {cp.last_updated_at ? new Date(cp.last_updated_at).toISOString() : 'None'}
                    </span>
                  </td>
                  <td style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>
                    {new Date(cp.updated_at).toLocaleTimeString()}
                  </td>
                  <td>
                    <StatusBadge status="ACTIVE" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Sync Runs Table (Scannable, clean, typography normal, no neon numbers) */}
      <div className="section-panel">
        <div className="section-panel-header">
          <div className="section-panel-title">Sync Runs (sync_runs)</div>
          <span className="hash-pill">{syncRuns.length} runs</span>
        </div>

        <div className="table-wrapper" style={{ margin: 0, border: 'none', borderRadius: 0 }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Run ID</th>
                <th>Stream</th>
                <th>Status</th>
                <th>Started</th>
                <th>Read</th>
                <th>Inserted</th>
                <th>Updated</th>
                <th>Skipped</th>
                <th>Failed</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {syncRuns.map((run) => (
                <tr key={run.id}>
                  <td>
                    <span className="hash-pill">{run.id.substring(0, 12)}...</span>
                  </td>
                  <td style={{ fontWeight: 600 }}>
                    {run.stream_name.toUpperCase()}
                  </td>
                  <td>
                    <StatusBadge status={run.status} />
                  </td>
                  <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                    {new Date(run.started_at).toLocaleTimeString()}
                  </td>
                  <td className="mono">{run.records_read}</td>
                  <td className="mono">{run.inserted}</td>
                  <td className="mono">{run.updated}</td>
                  <td className="mono">{run.skipped}</td>
                  <td className="mono" style={{ color: run.failed > 0 ? 'var(--status-error)' : 'inherit' }}>
                    {run.failed}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => setViewingRun(run)}
                    >
                      Details
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Drawer: Sync Run & Checkpoint Audit */}
      <Drawer
        isOpen={!!viewingRun}
        onClose={() => setViewingRun(null)}
        title="Sync Run Details"
        subtitle={viewingRun ? `ID: ${viewingRun.id}` : undefined}
      >
        {viewingRun && (
          <div>
            <div className="kv-grid" style={{ marginBottom: '16px' }}>
              <span className="kv-key">Stream:</span>
              <span className="kv-val mono">{viewingRun.stream_name.toUpperCase()}</span>

              <span className="kv-key">Status:</span>
              <span className="kv-val">
                <StatusBadge status={viewingRun.status} />
              </span>

              <span className="kv-key">Started:</span>
              <span className="kv-val">{viewingRun.started_at}</span>

              <span className="kv-key">Finished:</span>
              <span className="kv-val">{viewingRun.finished_at || 'In progress'}</span>

              <span className="kv-key">Records Read:</span>
              <span className="kv-val mono">{viewingRun.records_read}</span>

              <span className="kv-key">Inserted:</span>
              <span className="kv-val mono">{viewingRun.inserted}</span>

              <span className="kv-key">Updated:</span>
              <span className="kv-val mono">{viewingRun.updated}</span>

              <span className="kv-key">Skipped:</span>
              <span className="kv-val mono">{viewingRun.skipped}</span>

              <span className="kv-key">Failed:</span>
              <span className="kv-val mono">{viewingRun.failed}</span>
            </div>

            <div style={{ marginTop: '16px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '6px' }}>
                checkpoint_before:
              </div>
              <pre
                className="mono"
                style={{
                  background: 'var(--bg-app)',
                  padding: '8px 10px',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '11.5px',
                  border: '1px solid var(--border-default)',
                  overflowX: 'auto',
                }}
              >
                {JSON.stringify(viewingRun.checkpoint_before, null, 2) || 'null'}
              </pre>
            </div>

            <div style={{ marginTop: '14px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '6px' }}>
                checkpoint_after:
              </div>
              <pre
                className="mono"
                style={{
                  background: 'var(--bg-app)',
                  padding: '8px 10px',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '11.5px',
                  border: '1px solid var(--border-default)',
                  overflowX: 'auto',
                }}
              >
                {JSON.stringify(viewingRun.checkpoint_after, null, 2) || 'null'}
              </pre>
            </div>

            {viewingRun.error_message && (
              <div className="notice-box warning" style={{ marginTop: '16px' }}>
                <div className="notice-body">{viewingRun.error_message}</div>
              </div>
            )}
          </div>
        )}
      </Drawer>
    </div>
  );
};
