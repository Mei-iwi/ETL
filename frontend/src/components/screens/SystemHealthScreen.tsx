import React from 'react';
import { ApiGuide } from '../common/ApiGuide';
import { useEtl } from '../../context/EtlContext';
import { StatusBadge } from '../common/StatusBadge';
import {
  ArrowClockwise,
} from '@phosphor-icons/react';

export const SystemHealthScreen: React.FC = () => {
  const {
    health,
    backendStatus,
    checkBackendHealth,
    apiBaseUrl,
    isLiveMode,
  } = useEtl();

  const components = [
    {
      name: 'PostgreSQL Database',
      status: backendStatus === 'connected' ? 'CONNECTED' : 'DISCONNECTED',
      engine: 'PostgreSQL 16 (psycopg3)',
      details: health.postgres.detail || 'Kiểm tra qua GET /health.',
    },
    {
      name: 'FastAPI Web API',
      status: backendStatus === 'connected' ? 'RUNNING' : 'DISABLED',
      engine: 'Uvicorn ASGI',
      details: `Endpoint at ${apiBaseUrl}. Includes CorrelationMiddleware (X-Correlation-ID tracing).`,
    },
    {
      name: 'Source Adapter (Sync)',
      status: 'UNKNOWN',
      engine: 'SOURCE_ADAPTER=json',
      details: 'Reads streaming mock JSON fixtures. Note: SOURCE_ADAPTER=production is BLOCKED_SOURCE_MAPPING.',
    },
    {
      name: 'OCR Runtime Engine',
      status: 'UNKNOWN',
      engine: 'OCR_ENGINE=native (PyMuPDF)',
      details: 'Extracts native document text layer. Note: Scanned PDF OCR is not integrated (Tesseract/PaddleOCR unbundled).',
    },
    {
      name: 'Admin API Access',
      status: 'UNKNOWN',
      engine: 'Chưa xác định; kiểm tra bằng API admin',
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

      <ApiGuide key={isLiveMode ? 'live' : 'prototype'} />
    </div>
  );
};
