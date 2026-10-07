import React from 'react';
import { useEtl } from '../../context/EtlContext';
import { StatusBadge } from '../common/StatusBadge';
import { ProgressBar } from '../common/ProgressBar';
import { ArrowRight, Play } from '@phosphor-icons/react';

export const OverviewScreen: React.FC = () => {
  const {
    resources,
    versions,
    ocrJobs,
    contentUnits,
    syncRuns,
    stageRuns,
    health,
    backendStatus,
    setActiveScreen,
    setSelectedJobId,
  } = useEtl();

  return (
    <div>
      {/* Page Header */}
      <div className="page-header">
        <div className="page-title-row">
          <div>
            <h1 className="page-title">Operations Overview</h1>
            <p className="page-description">
              Master Data Sync & Learning Resource ETL pipeline monitoring. Pipeline terminates at Content Units.
            </p>
          </div>
          <div className="page-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setActiveScreen('sync')}
            >
              <Play size={13} weight="bold" /> Run Master Sync
            </button>
          </div>
        </div>
      </div>

      {/* 4 Summary Metrics Strip */}
      <div className="metrics-strip">
        <div className="metric-strip-item">
          <div className="metric-strip-label">Resources (Catalog)</div>
          <div className="metric-strip-value">{resources.length}</div>
          <div className="metric-strip-sub">Logical master entities</div>
        </div>

        <div className="metric-strip-item">
          <div className="metric-strip-label">Resource Versions</div>
          <div className="metric-strip-value">{versions.length}</div>
          <div className="metric-strip-sub">Immutable physical files</div>
        </div>

        <div className="metric-strip-item">
          <div className="metric-strip-label">OCR Jobs</div>
          <div className="metric-strip-value">{ocrJobs.length}</div>
          <div className="metric-strip-sub">
            {ocrJobs.filter((j) => j.status === 'COMPLETED').length} completed
          </div>
        </div>

        <div className="metric-strip-item">
          <div className="metric-strip-label">Content Units</div>
          <div className="metric-strip-value">{contentUnits.length}</div>
          <div className="metric-strip-sub">PAGE_TEXT normalized</div>
        </div>
      </div>

      {/* Linear Pipeline Flow */}
      <div className="card" style={{ marginBottom: '20px' }}>
        <div className="card-header">
          <div>
            <div className="card-title">ETL Pipeline Flow</div>
            <div className="card-subtitle">
              Linear data processing from master data synchronization to NFC-normalized Content Units
            </div>
          </div>
        </div>
        <div style={{ padding: '12px 16px' }}>
          <div className="pipeline-flow-compact">
            <div className="pipeline-step" onClick={() => setActiveScreen('sync')}>
              <span className="pipeline-step-num">1</span>
              <span className="pipeline-step-title">Master Sync</span>
              <span style={{ color: 'var(--text-muted)' }}>(Grades → Subjects → Users → Resources)</span>
            </div>

            <span className="pipeline-arrow">→</span>

            <div className="pipeline-step" onClick={() => setActiveScreen('resources')}>
              <span className="pipeline-step-num">2</span>
              <span className="pipeline-step-title">Resource Catalog</span>
            </div>

            <span className="pipeline-arrow">→</span>

            <div className="pipeline-step" onClick={() => setActiveScreen('ingest')}>
              <span className="pipeline-step-num">3</span>
              <span className="pipeline-step-title">Version Ingestion</span>
              <span style={{ color: 'var(--text-muted)' }}>(SHA-256)</span>
            </div>

            <span className="pipeline-arrow">→</span>

            <div className="pipeline-step" onClick={() => setActiveScreen('ocr')}>
              <span className="pipeline-step-num">4</span>
              <span className="pipeline-step-title">OCR Tasks</span>
              <span style={{ color: 'var(--text-muted)' }}>(PyMuPDF Native)</span>
            </div>

            <span className="pipeline-arrow">→</span>

            <div className="pipeline-step" onClick={() => setActiveScreen('postprocess')}>
              <span className="pipeline-step-num">5</span>
              <span className="pipeline-step-title">Postprocess</span>
              <span style={{ color: 'var(--text-muted)' }}>(NFC Normalization)</span>
            </div>

            <span className="pipeline-arrow">→</span>

            <div className="pipeline-step" onClick={() => setActiveScreen('content-units')}>
              <span className="pipeline-step-num">6</span>
              <span className="pipeline-step-title">Content Units</span>
              <span style={{ color: 'var(--text-muted)' }}>(PAGE_TEXT)</span>
            </div>
          </div>
        </div>
      </div>

      {/* System Infrastructure (Table format) */}
      <div className="card" style={{ marginBottom: '20px' }}>
        <div className="card-header">
          <div>
            <div className="card-title">System Infrastructure & Adapter Status</div>
            <div className="card-subtitle">
              Operational parameters and documented backend constraints
            </div>
          </div>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => setActiveScreen('health')}
          >
            Probe API
          </button>
        </div>

        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: '220px' }}>Component</th>
                <th style={{ width: '140px' }}>Status</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td style={{ fontWeight: 600 }}>PostgreSQL 16 Database</td>
                <td>
                  <StatusBadge status={backendStatus === 'connected' ? 'CONNECTED' : 'DISCONNECTED'} />
                </td>
                <td style={{ color: 'var(--text-secondary)' }}>
                  {health.postgres.detail || 'Port 5432, autocommit session engine, driver postgresql+psycopg.'}
                </td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>FastAPI Web Runtime</td>
                <td>
                  <StatusBadge status={backendStatus === 'connected' ? 'RUNNING' : 'DISABLED'} />
                </td>
                <td style={{ color: 'var(--text-secondary)' }}>
                  FastAPI on Uvicorn ASGI with CorrelationMiddleware (X-Correlation-ID tracing).
                </td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>Source Adapter (Sync)</td>
                <td>
                  <StatusBadge status="ACTIVE" />
                </td>
                <td style={{ color: 'var(--text-secondary)' }}>
                  <code className="mono">SOURCE_ADAPTER=json</code> (Streaming JSON fixtures). Production adapter is BLOCKED_SOURCE_MAPPING.
                </td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>OCR Engine Pipeline</td>
                <td>
                  <StatusBadge status="ACTIVE" />
                </td>
                <td style={{ color: 'var(--text-secondary)' }}>
                  <code className="mono">OCR_ENGINE=native</code> (PyMuPDF native text extraction). Scan engine is BLOCKED_OCR_RUNTIME.
                </td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>Admin API Access</td>
                <td>
                  <StatusBadge status="DISABLED (DEFAULT)" />
                </td>
                <td style={{ color: 'var(--text-secondary)' }}>
                  <code className="mono">ADMIN_ENABLED=false</code> by default. Endpoints return HTTP 403 Forbidden unless enabled in .env.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Activity Tables: Recent Sync Runs, Recent OCR Jobs, Recent ETL Stages */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px', marginBottom: '20px' }}>
        {/* Recent Sync Runs Table */}
        <div className="card">
          <div className="card-header">
            <div>
              <div className="card-title">Recent Sync Runs (sync_runs)</div>
              <div className="card-subtitle">Master data synchronization history</div>
            </div>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => setActiveScreen('sync')}
            >
              View all <ArrowRight size={12} />
            </button>
          </div>

          <div className="table-container">
            <table className="table">
              <thead>
                <tr>
                  <th>Stream</th>
                  <th>Status</th>
                  <th>Read</th>
                  <th>Counts (Ins/Upd/Skp)</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {syncRuns.slice(0, 4).map((run) => (
                  <tr key={run.id}>
                    <td>
                      <span className="mono" style={{ fontWeight: 600 }}>
                        {run.stream_name.toUpperCase()}
                      </span>
                    </td>
                    <td>
                      <StatusBadge status={run.status} />
                    </td>
                    <td className="mono">{run.records_read}</td>
                    <td className="mono" style={{ fontSize: '12px' }}>
                      {run.inserted} / {run.updated} / {run.skipped}
                    </td>
                    <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                      {new Date(run.started_at).toLocaleTimeString('vi-VN')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Recent OCR Jobs Table */}
        <div className="card">
          <div className="card-header">
            <div>
              <div className="card-title">Recent OCR Jobs (ocr_jobs)</div>
              <div className="card-subtitle">Page-level recognition progress</div>
            </div>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => setActiveScreen('ocr')}
            >
              View all <ArrowRight size={12} />
            </button>
          </div>

          <div className="table-container">
            <table className="table">
              <thead>
                <tr>
                  <th>Job ID</th>
                  <th>Status</th>
                  <th>Progress</th>
                  <th style={{ textAlign: 'right' }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {ocrJobs.slice(0, 4).map((job) => (
                  <tr key={job.id}>
                    <td>
                      <span className="mono" style={{ fontSize: '11px', fontWeight: 600 }}>
                        {job.id.substring(0, 14)}...
                      </span>
                    </td>
                    <td>
                      <StatusBadge status={job.status} />
                    </td>
                    <td style={{ minWidth: '120px' }}>
                      <ProgressBar
                        completed={job.completed_pages}
                        failed={job.failed_pages}
                        total={job.total_pages}
                      />
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => {
                          setSelectedJobId(job.id);
                          setActiveScreen('ocr', { jobId: job.id });
                        }}
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
      </div>

      {/* Recent ETL Stage Runs */}
      <div className="card">
        <div className="card-header">
          <div>
            <div className="card-title">Recent ETL Stage Runs (etl_stage_runs)</div>
            <div className="card-subtitle">Audit trails for postprocessing and normalization stages</div>
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => setActiveScreen('postprocess')}
          >
            View all <ArrowRight size={12} />
          </button>
        </div>

        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th>Run ID</th>
                <th>Resource Version</th>
                <th>Stage</th>
                <th>Status</th>
                <th>Coverage</th>
                <th>Fingerprint</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {stageRuns.slice(0, 4).map((stg) => (
                <tr key={stg.id}>
                  <td>
                    <span className="mono" style={{ fontSize: '11px' }}>
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
                  <td>
                    {stg.coverage ? (
                      <span className="mono" style={{ fontSize: '12px' }}>
                        {stg.coverage.successful_pages}/{stg.coverage.total_pages} pages
                      </span>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td>
                    <span className="mono" style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                      {stg.input_fingerprint ? `${stg.input_fingerprint.substring(0, 14)}...` : 'N/A'}
                    </span>
                  </td>
                  <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                    {new Date(stg.started_at).toLocaleTimeString('vi-VN')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
