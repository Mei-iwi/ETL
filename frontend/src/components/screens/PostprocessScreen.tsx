import React, { useState } from 'react';
import { useEtl } from '../../context/EtlContext';
import type { ETLStageRun } from '../../types/etl';
import { StatusBadge } from '../common/StatusBadge';
import { Drawer } from '../common/Drawer';
import {
  Play,
  ArrowRight,
  Eye,
  Warning,
} from '@phosphor-icons/react';

export const PostprocessScreen: React.FC = () => {
  const {
    stageRuns,
    versions,
    ocrJobs,
    selectedVersionId,
    triggerPostprocess,
    setActiveScreen,
    isLiveMode,
  } = useEtl();

  const [targetVersionId, setTargetVersionId] = useState<string>(
    selectedVersionId || (versions[0] ? versions[0].id : '')
  );
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [viewingStage, setViewingStage] = useState<ETLStageRun | null>(null);

  const selectedJob = ocrJobs.find((j) => j.resource_version_id === targetVersionId);
  const isTerminalJob =
    selectedJob && (selectedJob.status === 'COMPLETED' || selectedJob.status === 'COMPLETED_WITH_ERRORS');

  const handleRunPostprocess = async () => {
    if (!targetVersionId) return;
    setIsRunning(true);
    try {
      await triggerPostprocess(targetVersionId);
    } finally {
      setIsRunning(false);
    }
  };

  return (
    <div>
      <div className="page-header">
        <div className="page-title-row">
          <div>
            <h1 className="page-title">Postprocess Stages</h1>
            <p className="page-description">
              Transform terminal OCR page outputs into normalized Content Units. NFC normalization, whitespace cleanup, and input fingerprint generation.
            </p>
          </div>
          <div className="page-actions">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setActiveScreen('content-units')}
            >
              View Content Units <ArrowRight size={14} />
            </button>
          </div>
        </div>
      </div>

      {/* Trigger Postprocess Control Box */}
      <div className="card" style={{ marginBottom: '20px' }}>
        <div className="card-header">
          <div>
            <div className="card-title">Execute Postprocess Stage</div>
            <div className="card-subtitle">
              Select a resource version with completed OCR job to normalize and generate Content Units
            </div>
          </div>
          {isLiveMode && (
            <span className="mono" style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
              POST /admin/resource-versions/&#123;id&#125;/postprocess
            </span>
          )}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr auto', gap: '16px', alignItems: 'flex-end' }}>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="form-label">
              <span>Resource Version</span>
            </label>
            <select
              className="form-select mono"
              value={targetVersionId}
              onChange={(e) => setTargetVersionId(e.target.value)}
              disabled={isRunning}
            >
              {versions.map((v) => {
                const job = ocrJobs.find((j) => j.resource_version_id === v.id);
                return (
                  <option key={v.id} value={v.id}>
                    {v.id} (v{v.version_number}) — {v.resource_id} [OCR: {job ? job.status : 'No Job'}]
                  </option>
                );
              })}
            </select>
          </div>

          <div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px' }}>
              OCR Prerequisite Status:
            </div>
            {selectedJob ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <StatusBadge status={selectedJob.status} />
                <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                  {selectedJob.completed_pages}/{selectedJob.total_pages} pages finished
                </span>
              </div>
            ) : (
              <span className="badge badge-pending">NO OCR JOB</span>
            )}
          </div>

          <div>
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleRunPostprocess}
              disabled={isRunning || !isTerminalJob}
              style={{ height: '36px', padding: '0 16px' }}
            >
              <Play size={14} />
              {isRunning ? 'Running...' : 'Run Postprocess'}
            </button>
          </div>
        </div>

        {!isTerminalJob && selectedJob && (
          <div style={{ marginTop: '10px', fontSize: '12px', color: 'var(--status-warning)' }}>
            Selected OCR job is in state <span className="mono">{selectedJob.status}</span>. Postprocess requires terminal OCR status (COMPLETED or COMPLETED_WITH_ERRORS).
          </div>
        )}
      </div>

      {/* Stage Runs Audit History: TABLE FIRST */}
      <div className="card">
        <div className="card-header">
          <div>
            <div className="card-title">Stage Runs Audit Log (etl_stage_runs)</div>
            <div className="card-subtitle">
              Execution history, NFC fingerprints, and page coverage audit trails
            </div>
          </div>
          <span className="hash-pill">{stageRuns.length} runs</span>
        </div>

        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: '140px' }}>Run ID</th>
                <th>Resource Version</th>
                <th style={{ width: '130px' }}>Stage</th>
                <th style={{ width: '120px' }}>Status</th>
                <th style={{ width: '120px' }}>Started</th>
                <th style={{ width: '120px' }}>Finished</th>
                <th>Coverage</th>
                <th>Fingerprint</th>
                <th style={{ width: '90px', textAlign: 'right' }}>Audit</th>
              </tr>
            </thead>
            <tbody>
              {stageRuns.map((stg) => (
                <tr key={stg.id}>
                  <td>
                    <span className="mono" style={{ fontSize: '11px', fontWeight: 600 }}>
                      {stg.id.substring(0, 14)}...
                    </span>
                  </td>
                  <td>
                    <span className="mono" style={{ fontSize: '12px' }}>
                      {stg.resource_version_id}
                    </span>
                  </td>
                  <td>
                    <span className="mono" style={{ fontWeight: 600 }}>{stg.stage}</span>
                  </td>
                  <td>
                    <StatusBadge status={stg.status} />
                  </td>
                  <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                    {new Date(stg.started_at).toLocaleTimeString('vi-VN')}
                  </td>
                  <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                    {stg.finished_at ? new Date(stg.finished_at).toLocaleTimeString('vi-VN') : '—'}
                  </td>
                  <td>
                    {stg.coverage ? (
                      <span className="mono" style={{ fontSize: '12px' }}>
                        {stg.coverage.successful_pages}/{stg.coverage.total_pages} ({stg.coverage.failed_pages} err)
                      </span>
                    ) : (
                      <span style={{ color: 'var(--text-muted)' }}>—</span>
                    )}
                  </td>
                  <td>
                    <span
                      className="mono"
                      style={{ fontSize: '11px', color: 'var(--text-secondary)' }}
                      title={stg.input_fingerprint || ''}
                    >
                      {stg.input_fingerprint ? `${stg.input_fingerprint.substring(0, 14)}...` : 'N/A'}
                    </span>
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => setViewingStage(stg)}
                    >
                      <Eye size={12} /> Inspect
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Drawer: Stage Run Inspection */}
      <Drawer
        isOpen={!!viewingStage}
        onClose={() => setViewingStage(null)}
        title="Stage Run Audit"
        subtitle={viewingStage ? `Run ID: ${viewingStage.id}` : undefined}
      >
        {viewingStage && (
          <div>
            <div className="kv-grid" style={{ marginBottom: '18px' }}>
              <span className="kv-key">Stage Name:</span>
              <span className="kv-val mono" style={{ fontWeight: 600 }}>{viewingStage.stage}</span>

              <span className="kv-key">Version ID:</span>
              <span className="kv-val mono">{viewingStage.resource_version_id}</span>

              <span className="kv-key">Status:</span>
              <span className="kv-val">
                <StatusBadge status={viewingStage.status} />
              </span>

              <span className="kv-key">Started:</span>
              <span className="kv-val">{viewingStage.started_at}</span>

              <span className="kv-key">Finished:</span>
              <span className="kv-val">{viewingStage.finished_at || 'Running'}</span>

              <span className="kv-key">Coverage:</span>
              <span className="kv-val mono">
                {viewingStage.coverage
                  ? `${viewingStage.coverage.successful_pages}/${viewingStage.coverage.total_pages} pages (${viewingStage.coverage.failed_pages} failed)`
                  : 'N/A'}
              </span>
            </div>

            <div style={{ marginTop: '16px' }}>
              <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '6px', color: 'var(--text-primary)' }}>
                Input Fingerprint (SHA-256)
              </div>
              <pre
                className="mono"
                style={{
                  background: 'var(--bg-app)',
                  padding: '10px 12px',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '11px',
                  border: '1px solid var(--border-default)',
                  wordBreak: 'break-all',
                }}
              >
                {viewingStage.input_fingerprint || 'Not computed'}
              </pre>
            </div>

            {viewingStage.error_message && (
              <div className="notice-box warning" style={{ marginTop: '16px' }}>
                <Warning size={16} color="var(--status-error)" style={{ flexShrink: 0 }} />
                <div>
                  <div className="notice-title">Error Message</div>
                  <div className="notice-body">{viewingStage.error_message}</div>
                </div>
              </div>
            )}
          </div>
        )}
      </Drawer>
    </div>
  );
};
