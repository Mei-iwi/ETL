import React from 'react';
import { useEtl } from '../../context/EtlContext';
import { Sun, Moon, Play } from '@phosphor-icons/react';

export const Header: React.FC = () => {
  const {
    activeScreen,
    setActiveScreen,
    theme,
    toggleTheme,
    isLiveMode,
    setIsLiveMode,
    backendStatus,
    checkBackendHealth,
  } = useEtl();

  const getScreenBreadcrumb = () => {
    switch (activeScreen) {
      case 'overview':
        return 'Overview';
      case 'sync':
        return 'Master Sync';
      case 'resources':
        return 'Learning Resources';
      case 'resource-detail':
        return 'Resource Detail & Versions';
      case 'ingest':
        return 'Resource Ingestion';
      case 'ocr':
        return 'OCR Jobs & Tasks';
      case 'operations':
        return 'Workers & Recovery';
      case 'postprocess':
        return 'Postprocess Stages';
      case 'content-units':
        return 'Content Units';
      case 'health':
        return 'System Health';
      default:
        return 'Operations';
    }
  };

  return (
    <header className="header">
      <div className="header-left">
        <div className="breadcrumb-trail">
          <span className="breadcrumb-root">Console</span>
          <span className="breadcrumb-sep">/</span>
          <span className="current">{getScreenBreadcrumb()}</span>
        </div>
      </div>

      <div className="header-right">
        {/* Run Sync quick action */}
        {activeScreen !== 'sync' && (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => setActiveScreen('sync')}
          >
            <Play size={12} weight="bold" />
            <span>Run Sync</span>
          </button>
        )}

        {/* Runtime Environment Selector */}
        <div className="env-pill">
          <span
            className={`status-indicator ${backendStatus === 'connected' ? 'online' : 'offline'}`}
            title={backendStatus === 'connected' ? 'PostgreSQL & Backend Online' : 'Backend Disconnected'}
          />
          <select
            className="env-select"
            value={isLiveMode ? 'live' : 'mock'}
            onChange={(e) => {
              const live = e.target.value === 'live';
              setIsLiveMode(live);
              if (live) checkBackendHealth();
            }}
            title="Switch execution store / API runtime"
          >
            <option value="mock">Prototype Store (Local)</option>
            <option value="live">FastAPI Live API (8000)</option>
          </select>
        </div>

        {/* Admin Policy Notice */}
        <span className="admin-status-note" title="Write endpoints under /admin/* reject with HTTP 403 Forbidden unless ADMIN_ENABLED=true">
          ADMIN_ENABLED=false
        </span>

        {/* Minimalist Theme Toggle Icon Button */}
        <button
          type="button"
          className="theme-icon-btn"
          onClick={toggleTheme}
          title={theme === 'light' ? 'Switch to Dark Mode' : 'Switch to Light Mode'}
          aria-label="Toggle theme"
        >
          {theme === 'light' ? <Moon size={15} /> : <Sun size={15} />}
        </button>
      </div>
    </header>
  );
};
