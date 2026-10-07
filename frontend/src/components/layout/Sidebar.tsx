import React from 'react';
import { useEtl } from '../../context/EtlContext';
import type { ScreenId } from '../../context/EtlContext';
import {
  Gauge,
  ArrowsClockwise,
  Books,
  UploadSimple,
  Scan,
  GitFork,
  FileText,
  Cpu,
  HardDrives,
  Database,
} from '@phosphor-icons/react';

interface NavItemDef {
  id: ScreenId;
  label: string;
  icon: React.ReactNode;
  badge?: number | string;
}

interface NavSectionDef {
  sectionLabel?: string;
  items: NavItemDef[];
}

export const Sidebar: React.FC = () => {
  const {
    activeScreen,
    setActiveScreen,
    resources,
    versions,
    ocrJobs,
    contentUnits,
    backendStatus,
  } = useEtl();

  const navSections: NavSectionDef[] = [
    {
      items: [
        {
          id: 'overview',
          label: 'Overview',
          icon: <Gauge size={15} />,
        },
      ],
    },
    {
      sectionLabel: 'WORKFLOWS',
      items: [
        {
          id: 'sync',
          label: 'Master Sync',
          icon: <ArrowsClockwise size={15} />,
        },
      ],
    },
    {
      sectionLabel: 'RESOURCE ETL',
      items: [
        {
          id: 'resources',
          label: 'Learning Resources',
          icon: <Books size={15} />,
          badge: resources.length,
        },
        {
          id: 'ingest',
          label: 'Ingestion',
          icon: <UploadSimple size={15} />,
          badge: versions.length,
        },
        {
          id: 'ocr',
          label: 'OCR Jobs',
          icon: <Scan size={15} />,
          badge: ocrJobs.length,
        },
        {
          id: 'postprocess',
          label: 'Postprocess',
          icon: <GitFork size={15} />,
        },
        {
          id: 'content-units',
          label: 'Content Units',
          icon: <FileText size={15} />,
          badge: contentUnits.length,
        },
      ],
    },
    {
      sectionLabel: 'OPERATIONS',
      items: [
        {
          id: 'operations',
          label: 'Workers & Recovery',
          icon: <Cpu size={15} />,
        },
        {
          id: 'health',
          label: 'System Health',
          icon: <HardDrives size={15} />,
        },
      ],
    },
  ];

  return (
    <aside className="sidebar">
      {/* Clean enterprise brand lockup */}
      <div className="sidebar-header">
        <div className="brand-lockup">
          <Database size={16} weight="bold" className="brand-icon" />
          <span className="brand-title">ETL Console</span>
          <span className="brand-env-tag">DataOps</span>
        </div>
      </div>

      <nav className="sidebar-nav">
        {navSections.map((section, sIdx) => (
          <div key={sIdx} style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
            {section.sectionLabel && (
              <div className="nav-section-label">{section.sectionLabel}</div>
            )}
            {section.items.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`nav-item ${activeScreen === item.id ? 'active' : ''}`}
                onClick={() => setActiveScreen(item.id)}
              >
                <span className="icon">{item.icon}</span>
                <span>{item.label}</span>
                {item.badge !== undefined && (
                  <span className="item-badge">{item.badge}</span>
                )}
              </button>
            ))}
          </div>
        ))}
      </nav>

      <div className="sidebar-footer">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', color: 'var(--text-muted)' }}>
          <span>PostgreSQL 16</span>
          <span className="mono" style={{ fontSize: '11px', color: backendStatus === 'connected' ? 'var(--status-success)' : 'var(--text-muted)' }}>
            {backendStatus === 'connected' ? 'ONLINE' : 'OFFLINE'}
          </span>
        </div>
      </div>
    </aside>
  );
};
