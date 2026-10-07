import React, { useState } from 'react';
import { useEtl } from '../../context/EtlContext';
import { StatusBadge } from '../common/StatusBadge';
import { Drawer } from '../common/Drawer';
import {
  ArrowLeft,
  UploadSimple,
  Scan,
  Info,
} from '@phosphor-icons/react';

export const ResourceDetailScreen: React.FC = () => {
  const {
    resources,
    versions,
    ocrJobs,
    selectedResourceId,
    setActiveScreen,
    triggerCreateOcrJob,
    fetchEtlStatus,
  } = useEtl();

  const [etlStatusData, setEtlStatusData] = useState<any>(null);
  const [isInspecting, setIsInspecting] = useState(false);

  const resource = resources.find((r) => r.id === selectedResourceId) || resources[0];
  const resourceVersions = versions
    .filter((v) => v.resource_id === resource.id)
    .sort((a, b) => b.version_number - a.version_number);

  const handleInspectStatus = async (versionId: string) => {
    setIsInspecting(true);
    try {
      const data = await fetchEtlStatus(versionId);
      setEtlStatusData(data);
    } finally {
      setIsInspecting(false);
    }
  };

  return (
    <div>
      {/* Back button */}
      <div style={{ marginBottom: '14px' }}>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => setActiveScreen('resources')}
          style={{ paddingLeft: 0, color: 'var(--text-secondary)' }}
        >
          <ArrowLeft size={14} /> Back to Learning Resources
        </button>
      </div>

      {/* Page Header */}
      <div className="page-header" style={{ marginBottom: '20px' }}>
        <div className="page-title-row">
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
              <span className="hash-pill" style={{ fontSize: '12px', fontWeight: 600 }}>
                {resource.id}
              </span>
              <StatusBadge status={resource.publication_status} />
              <StatusBadge status={resource.is_active ? 'ACTIVE' : 'DISABLED'} />
            </div>
            <h1 className="page-title">{resource.title}</h1>
            <p className="page-description">
              Provider: {resource.provider_name} ({resource.provider_type}) · Type: {resource.resource_type_name_vi} ({resource.resource_type_code})
            </p>
          </div>

          <div className="page-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setActiveScreen('ingest')}
            >
              <UploadSimple size={15} /> Ingest Version
            </button>
          </div>
        </div>
      </div>

      {/* Resource Overview & Specification */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: '20px', marginBottom: '24px' }}>
        {/* Logical Resource Metadata */}
        <div className="card">
          <div className="card-header">
            <div>
              <div className="card-title">Catalog Metadata (master_learning_resources)</div>
              <div className="card-subtitle">Logical entity attributes synchronized from master data</div>
            </div>
          </div>

          <div className="kv-grid">
            <span className="kv-key">Resource ID:</span>
            <span className="kv-val mono" style={{ fontWeight: 600 }}>{resource.id}</span>

            <span className="kv-key">Title:</span>
            <span className="kv-val">{resource.title}</span>

            <span className="kv-key">Provider:</span>
            <span className="kv-val">{resource.provider_name} ({resource.provider_id})</span>

            <span className="kv-key">Resource Type:</span>
            <span className="kv-val">{resource.resource_type_name_vi} ({resource.resource_type_code})</span>

            <span className="kv-key">Subjects:</span>
            <span className="kv-val">
              {resource.subjects.length > 0
                ? resource.subjects.map((s) => s.name).join(', ')
                : <span style={{ color: 'var(--text-muted)' }}>None assigned</span>}
            </span>

            <span className="kv-key">Grade Levels:</span>
            <span className="kv-val">
              {resource.grades.length > 0
                ? resource.grades.map((g) => g.name_vi).join(', ')
                : <span style={{ color: 'var(--text-muted)' }}>None assigned</span>}
            </span>

            <span className="kv-key">Created:</span>
            <span className="kv-val">{new Date(resource.created_at).toLocaleString('vi-VN')}</span>

            <span className="kv-key">Updated:</span>
            <span className="kv-val">{new Date(resource.updated_at).toLocaleString('vi-VN')}</span>
          </div>
        </div>

        {/* Ingestion & Immutability Spec */}
        <div className="card">
          <div className="card-header">
            <div>
              <div className="card-title">Ingestion & Versioning Rules</div>
              <div className="card-subtitle">Physical immutability & deduplication logic</div>
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', fontSize: '13px', color: 'var(--text-secondary)' }}>
            <div style={{ padding: '10px 12px', background: 'var(--bg-app)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-sm)' }}>
              <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: '3px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                <Info size={14} /> Streaming SHA-256 Hashing
              </div>
              <div>Uploaded files are hashed in 1 MiB chunks. The physical version identity is strictly anchored by binary content, not filename or timestamp.</div>
            </div>

            <div style={{ padding: '10px 12px', background: 'var(--bg-app)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-sm)' }}>
              <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: '3px' }}>
                Deduplication vs. Evolution
              </div>
              <div>
                • <strong>Matching latest hash:</strong> Ingestion returns <code>NO_CHANGE</code> and halts without duplicating records.<br />
                • <strong>Different hash:</strong> Creates <code>v(N+1)</code> with <code>parent_version_id = latest.id</code>.
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Physical Version History: TABLE FIRST */}
      <div className="card">
        <div className="card-header">
          <div>
            <div className="card-title">Physical Version History (resource_versions)</div>
            <div className="card-subtitle">
              Logical Resource (1) → Physical Versions (N) with immutability guarantees
            </div>
          </div>
          <span className="hash-pill">{resourceVersions.length} versions</span>
        </div>

        {resourceVersions.length === 0 ? (
          <div style={{ padding: '36px', textAlign: 'center', color: 'var(--text-muted)' }}>
            No physical versions have been ingested for this resource yet.
          </div>
        ) : (
          <div className="table-container">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: '85px' }}>Version</th>
                  <th style={{ width: '130px' }}>Version ID</th>
                  <th style={{ width: '120px' }}>Status</th>
                  <th>Storage Location</th>
                  <th>SHA-256 Hash</th>
                  <th style={{ width: '120px' }}>Parent Version</th>
                  <th style={{ width: '140px' }}>Uploaded</th>
                  <th style={{ width: '180px', textAlign: 'right' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {resourceVersions.map((ver, idx) => {
                  const job = ocrJobs.find((j) => j.resource_version_id === ver.id);
                  const isLatest = idx === 0;

                  return (
                    <tr key={ver.id}>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <span className="mono" style={{ fontWeight: 700 }}>v{ver.version_number}</span>
                          {isLatest && (
                            <span style={{ fontSize: '10px', background: 'var(--bg-app)', border: '1px solid var(--border-default)', padding: '1px 4px', borderRadius: '3px', fontWeight: 600 }}>
                              LATEST
                            </span>
                          )}
                        </div>
                      </td>
                      <td>
                        <span className="mono" style={{ fontSize: '12px', fontWeight: 600 }}>{ver.id}</span>
                      </td>
                      <td>
                        <StatusBadge status={ver.lifecycle_status} />
                      </td>
                      <td>
                        <span className="mono" style={{ fontSize: '12px' }}>
                          {ver.storage_bucket}/{ver.storage_object_key}
                        </span>
                      </td>
                      <td>
                        <span
                          className="mono"
                          style={{ fontSize: '11px', color: 'var(--text-secondary)' }}
                          title={ver.source_hash}
                        >
                          {ver.source_hash ? `${ver.source_hash.slice(0, 16)}...` : 'N/A'}
                        </span>
                      </td>
                      <td>
                        <span className="mono" style={{ fontSize: '12px' }}>
                          {ver.parent_version_id || <span style={{ color: 'var(--text-muted)' }}>Root (v1)</span>}
                        </span>
                      </td>
                      <td>
                        <div style={{ fontSize: '12px' }}>
                          <div>{new Date(ver.created_at).toLocaleDateString('vi-VN')}</div>
                          <div style={{ color: 'var(--text-muted)', fontSize: '11px' }}>{ver.uploaded_by || 'system'}</div>
                        </div>
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        <div style={{ display: 'inline-flex', gap: '6px', alignItems: 'center' }}>
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            disabled={isInspecting}
                            onClick={() => handleInspectStatus(ver.id)}
                            title="Inspect pipeline state"
                          >
                            Status
                          </button>
                          {!job ? (
                            <button
                              type="button"
                              className="btn btn-secondary btn-sm"
                              onClick={() => triggerCreateOcrJob(ver.id)}
                            >
                              <Scan size={13} /> OCR
                            </button>
                          ) : (
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm"
                              onClick={() => setActiveScreen('ocr', { jobId: job.id })}
                            >
                              Job ({job.status})
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Drawer: ETL Status Inspection */}
      <Drawer
        isOpen={!!etlStatusData}
        onClose={() => setEtlStatusData(null)}
        title="ETL Status Probe"
        subtitle={`GET /admin/resource-versions/${etlStatusData?.version?.id}/etl-status`}
      >
        {etlStatusData && (
          <div>
            <div className="kv-grid" style={{ marginBottom: '20px' }}>
              <span className="kv-key">Resource ID:</span>
              <span className="kv-val mono">{etlStatusData.resource?.id}</span>

              <span className="kv-key">Version ID:</span>
              <span className="kv-val mono">{etlStatusData.version?.id} (v{etlStatusData.version?.version_number})</span>

              <span className="kv-key">Latest Version:</span>
              <span className="kv-val mono">v{etlStatusData.latest_version?.version_number}</span>

              <span className="kv-key">OCR Job Status:</span>
              <span className="kv-val">
                {etlStatusData.ocr_job ? (
                  <StatusBadge status={etlStatusData.ocr_job.status} />
                ) : (
                  <span style={{ color: 'var(--text-muted)' }}>No job created</span>
                )}
              </span>

              <span className="kv-key">Content Units:</span>
              <span className="kv-val mono" style={{ fontWeight: 600 }}>
                {etlStatusData.content_unit_count} units
              </span>
            </div>

            <div style={{ marginTop: '20px' }}>
              <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px', color: 'var(--text-primary)' }}>
                Stage Execution History (etl_stage_runs)
              </div>
              {!etlStatusData.stage_runs || etlStatusData.stage_runs.length === 0 ? (
                <div style={{ color: 'var(--text-muted)', fontSize: '12px' }}>
                  No stage runs recorded for this version.
                </div>
              ) : (
                <div className="table-container">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Stage</th>
                        <th>Status</th>
                        <th>Fingerprint</th>
                        <th>Executed</th>
                      </tr>
                    </thead>
                    <tbody>
                      {etlStatusData.stage_runs.map((stg: any) => (
                        <tr key={stg.id}>
                          <td className="mono" style={{ fontWeight: 600 }}>{stg.stage}</td>
                          <td><StatusBadge status={stg.status} /></td>
                          <td className="mono" style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                            {stg.input_fingerprint ? `${stg.input_fingerprint.slice(0, 16)}...` : 'N/A'}
                          </td>
                          <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                            {stg.created_at ? new Date(stg.created_at).toLocaleTimeString('vi-VN') : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
};
