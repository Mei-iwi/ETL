import React, { useState } from 'react';
import { useEtl } from '../../context/EtlContext';
import { StatusBadge } from '../common/StatusBadge';
import { api } from '../../services/apiClient';
import {
  ArrowClockwise,
  Play,
} from '@phosphor-icons/react';

export const SystemHealthScreen: React.FC = () => {
  const {
    health,
    backendStatus,
    checkBackendHealth,
    apiBaseUrl,
    versions,
  } = useEtl();

  const [testingEndpoint, setTestingEndpoint] = useState<string>('/health');
  const [probeResult, setProbeResult] = useState<any>(null);
  const [isProbing, setIsProbing] = useState<boolean>(false);

  const testEndpoints = [
    { label: 'GET /health', path: '/health' },
    { label: 'POST /admin/sync/master', path: '/admin/sync/master' },
    {
      label: 'POST /admin/resources/resource-1/versions/ingest',
      path: '/admin/resources/resource-1/versions/ingest',
    },
    {
      label: 'GET /admin/resource-versions/{id}/etl-status',
      path: '/admin/resource-versions/ver-01JJ8A01A01B01C01D01E00001/etl-status',
    },
  ];

  const handleRunProbe = async (endpointPath: string) => {
    setIsProbing(true);
    setTestingEndpoint(endpointPath);
    try {
      if (endpointPath === '/health') {
        const res = await api.getHealth();
        setProbeResult(res);
      } else if (endpointPath === '/admin/sync/master') {
        const res = await api.runMasterSync('resources', false);
        setProbeResult(res);
      } else if (endpointPath.includes('/ingest')) {
        const res = await api.ingestResource('resource-1', 'input', 'sample.pdf', 'tester');
        setProbeResult(res);
      } else if (endpointPath.includes('/etl-status')) {
        const vId = versions[0] ? versions[0].id : 'ver-01JJ8A01A01B01C01D01E00001';
        const res = await api.getEtlStatus(vId);
        setProbeResult(res);
      }
    } finally {
      setIsProbing(false);
    }
  };

  const components = [
    {
      name: 'PostgreSQL Database',
      status: backendStatus === 'connected' ? 'CONNECTED' : 'DISCONNECTED',
      engine: 'PostgreSQL 16 (psycopg3)',
      details: health.postgres.detail || 'Connected on port 5432 with autocommit session engine.',
    },
    {
      name: 'FastAPI Web API',
      status: backendStatus === 'connected' ? 'RUNNING' : 'DISABLED',
      engine: 'Uvicorn ASGI',
      details: `Endpoint at ${apiBaseUrl}. Includes CorrelationMiddleware (X-Correlation-ID tracing).`,
    },
    {
      name: 'Source Adapter (Sync)',
      status: 'ACTIVE',
      engine: 'SOURCE_ADAPTER=json',
      details: 'Reads streaming mock JSON fixtures. Note: SOURCE_ADAPTER=production is BLOCKED_SOURCE_MAPPING.',
    },
    {
      name: 'OCR Runtime Engine',
      status: 'ACTIVE',
      engine: 'OCR_ENGINE=native (PyMuPDF)',
      details: 'Extracts native document text layer. Note: OCR_ENGINE=scan is BLOCKED_OCR_RUNTIME (Tesseract/PaddleOCR unbundled).',
    },
    {
      name: 'Admin API Access',
      status: 'DISABLED',
      engine: 'ADMIN_ENABLED=false (Default)',
      details: 'Write endpoints under /admin/* reject with HTTP 403 Forbidden unless ADMIN_ENABLED=true in .env.',
    },
  ];

  return (
    <div>
      <div className="page-header">
        <div className="page-title-row">
          <div>
            <h1 className="page-title">System Health & Infrastructure</h1>
            <p className="page-description">
              Operational status of PostgreSQL 16, FastAPI backend, source adapter drivers, and OCR engines with documented constraints.
            </p>
          </div>
          <div className="page-actions">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={checkBackendHealth}
            >
              <ArrowClockwise size={14} /> Recheck Health
            </button>
          </div>
        </div>
      </div>

      {/* Infrastructure Components: TABLE FIRST */}
      <div className="card" style={{ marginBottom: '20px' }}>
        <div className="card-header">
          <div>
            <div className="card-title">Core Subsystems</div>
            <div className="card-subtitle">Active infrastructure dependencies and operational limitations</div>
          </div>
          <span className="hash-pill">5 components</span>
        </div>

        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: '220px' }}>Component</th>
                <th style={{ width: '130px' }}>Status</th>
                <th style={{ width: '240px' }}>Driver / Mode</th>
                <th>Technical Details & Boundaries</th>
              </tr>
            </thead>
            <tbody>
              {components.map((comp) => (
                <tr key={comp.name}>
                  <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                    {comp.name}
                  </td>
                  <td>
                    <StatusBadge status={comp.status} />
                  </td>
                  <td>
                    <span className="mono" style={{ fontSize: '12px' }}>
                      {comp.engine}
                    </span>
                  </td>
                  <td style={{ fontSize: '12.5px', color: 'var(--text-secondary)' }}>
                    {comp.details}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Live API Contract Probe */}
      <div className="card">
        <div className="card-header">
          <div>
            <div className="card-title">Live API Contract Probe</div>
            <div className="card-subtitle">
              Dispatch actual HTTP requests to backend to inspect status code, headers, and payloads
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '16px' }}>
          {testEndpoints.map((ep) => (
            <button
              key={ep.path}
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => handleRunProbe(ep.path)}
              disabled={isProbing}
            >
              <Play size={12} /> {ep.label}
            </button>
          ))}
        </div>

        {/* Probe Response View */}
        <div style={{ background: 'var(--bg-app)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-default)', padding: '14px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Endpoint:</span>
              <code className="mono" style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
                {testingEndpoint}
              </code>
            </div>

            {probeResult && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>HTTP Status:</span>
                <span
                  className="mono"
                  style={{
                    fontWeight: 700,
                    color:
                      probeResult.status === 200
                        ? 'var(--status-success)'
                        : probeResult.status === 403
                        ? 'var(--status-warning)'
                        : 'var(--status-error)',
                  }}
                >
                  {probeResult.status}
                </span>
                {probeResult.correlationId && (
                  <span className="mono" style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                    X-Correlation-ID: {probeResult.correlationId.substring(0, 14)}...
                  </span>
                )}
              </div>
            )}
          </div>

          <pre
            className="mono"
            style={{
              padding: '12px',
              background: 'var(--bg-surface)',
              borderRadius: 'var(--radius-sm)',
              fontSize: '12px',
              border: '1px solid var(--border-default)',
              minHeight: '100px',
              maxHeight: '300px',
              overflowY: 'auto',
              color: 'var(--text-primary)',
            }}
          >
            {isProbing
              ? 'Dispatching request to backend server...'
              : probeResult
              ? JSON.stringify(probeResult, null, 2)
              : 'Select an endpoint probe above to inspect live API contract response.'}
          </pre>
        </div>
      </div>
    </div>
  );
};
