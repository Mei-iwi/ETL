import React from 'react';
import { useEtl } from '../../context/EtlContext';
import { StatusBadge } from './StatusBadge';

export const ApiBanner: React.FC = () => {
  const {
    isLiveMode,
    setIsLiveMode,
    backendStatus,
    lastCorrelationId,
    apiBaseUrl,
    setApiBaseUrl,
    checkBackendHealth,
  } = useEtl();

  return (
    <div
      style={{
        background: 'var(--bg-surface)',
        borderBottom: '1px solid var(--border-default)',
        padding: '6px 32px',
        fontSize: '12px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: '8px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <span style={{ color: 'var(--text-muted)', fontWeight: 500 }}>Target:</span>

        <div className="theme-toggle" style={{ height: '26px' }}>
          <button
            type="button"
            className={`btn ${!isLiveMode ? 'btn-primary' : 'btn-secondary'} btn-sm`}
            onClick={() => setIsLiveMode(false)}
            style={{ padding: '2px 8px', fontSize: '11.5px' }}
          >
            Mô phỏng (không ghi CSDL)
          </button>
          <button
            type="button"
            className={`btn ${isLiveMode ? 'btn-primary' : 'btn-secondary'} btn-sm`}
            onClick={() => {
              setIsLiveMode(true);
              checkBackendHealth();
            }}
            style={{ padding: '2px 8px', fontSize: '11.5px' }}
          >
            Live Backend API
          </button>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          {backendStatus === 'connected' && <StatusBadge status="CONNECTED" />}
          {backendStatus === 'disconnected' && <StatusBadge status="DISCONNECTED" />}
          {backendStatus === 'db_error' && <StatusBadge status="503 UNAVAILABLE" />}
          {backendStatus === 'checking' && <StatusBadge status="PENDING" />}
        </div>

        {lastCorrelationId && (
          <span style={{ color: 'var(--text-muted)', fontSize: '11px', fontFamily: 'var(--font-mono)' }}>
            cid: {lastCorrelationId.substring(0, 10)}...
          </span>
        )}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '11.5px' }}>
        {isLiveMode && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
            <input
              type="text"
              value={apiBaseUrl}
              onChange={(e) => setApiBaseUrl(e.target.value)}
              className="form-input mono"
              style={{ height: '26px', padding: '2px 8px', fontSize: '11px', width: '180px' }}
              aria-label="API Base URL"
              placeholder="Để trống: dùng proxy Vite"
            />
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              style={{ height: '26px' }}
              onClick={checkBackendHealth}
            >
              Kiểm tra kết nối
            </button>
          </div>
        )}

        <span style={{ color: 'var(--text-muted)' }}>
          {isLiveMode ? 'API thật · Danh mục vẫn là mẫu · Admin cần ADMIN_ENABLED=true' : 'Dữ liệu mẫu · không xử lý PDF thật'}
        </span>
      </div>
    </div>
  );
};
