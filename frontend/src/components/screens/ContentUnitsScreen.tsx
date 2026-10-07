import React, { useState } from 'react';
import { useEtl } from '../../context/EtlContext';
import type { ContentUnit } from '../../types/etl';
import { StatusBadge } from '../common/StatusBadge';
import { Drawer } from '../common/Drawer';
import {
  MagnifyingGlass,
  Eye,
} from '@phosphor-icons/react';

export const ContentUnitsScreen: React.FC = () => {
  const {
    contentUnits,
    versions,
  } = useEtl();

  const [selectedVersionFilter, setSelectedVersionFilter] = useState('all');
  const [searchTerm, setSearchTerm] = useState('');
  const [viewingUnit, setViewingUnit] = useState<ContentUnit | null>(null);

  const filteredUnits = contentUnits.filter((u) => {
    const matchesVer =
      selectedVersionFilter === 'all' || u.resource_version_id === selectedVersionFilter;
    const matchesSearch =
      u.id.toLowerCase().includes(searchTerm.toLowerCase()) ||
      u.text.toLowerCase().includes(searchTerm.toLowerCase());
    return matchesVer && matchesSearch;
  });

  return (
    <div>
      <div className="page-header">
        <div className="page-title-row">
          <div>
            <h1 className="page-title">Content Units</h1>
            <p className="page-description">
              Extracted and normalized document units (PAGE_TEXT) produced by postprocessing. Terminal ETL stage with explicit provenance tracking.
            </p>
          </div>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div
        className="card"
        style={{
          padding: '12px 16px',
          marginBottom: '20px',
          display: 'flex',
          gap: '12px',
          alignItems: 'center',
          flexWrap: 'wrap',
        }}
      >
        <div style={{ flex: 1, minWidth: '220px', position: 'relative' }}>
          <input
            type="text"
            className="form-input"
            style={{ width: '100%', paddingLeft: '32px' }}
            placeholder="Search by Unit ID or text content..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
          <MagnifyingGlass
            size={15}
            style={{ position: 'absolute', left: '10px', top: '10px', color: 'var(--text-muted)' }}
          />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Version:</span>
          <select
            className="form-select mono"
            style={{ padding: '6px 10px', fontSize: '12px' }}
            value={selectedVersionFilter}
            onChange={(e) => setSelectedVersionFilter(e.target.value)}
          >
            <option value="all">All Versions ({versions.length})</option>
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                {v.id} (v{v.version_number})
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Content Units Table: TABLE FIRST */}
      <div className="card">
        <div className="card-header">
          <div>
            <div className="card-title">Content Units (content_units & content_unit_texts)</div>
            <div className="card-subtitle">
              Physical text units with 1:1 text storage and page-level provenance
            </div>
          </div>
          <span className="hash-pill">{filteredUnits.length} units</span>
        </div>

        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: '60px' }}>Seq</th>
                <th style={{ width: '140px' }}>Unit ID</th>
                <th>Resource Version</th>
                <th style={{ width: '110px' }}>Type</th>
                <th style={{ width: '85px' }}>Pages</th>
                <th>Text Preview</th>
                <th style={{ width: '120px' }}>Review Status</th>
                <th style={{ width: '80px' }}>Eligible</th>
                <th style={{ width: '80px', textAlign: 'right' }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {filteredUnits.length === 0 ? (
                <tr>
                  <td colSpan={9} style={{ textAlign: 'center', padding: '36px', color: 'var(--text-muted)' }}>
                    No content units matching the current filter.
                  </td>
                </tr>
              ) : (
                filteredUnits.map((unit) => (
                  <tr key={unit.id}>
                    <td>
                      <span className="mono" style={{ fontWeight: 600 }}>#{unit.sequence_no}</span>
                    </td>
                    <td>
                      <span className="mono" style={{ fontSize: '12px', fontWeight: 600 }}>{unit.id}</span>
                    </td>
                    <td>
                      <span className="mono" style={{ fontSize: '12px' }}>
                        {unit.resource_version_id}
                      </span>
                    </td>
                    <td>
                      <span className="mono" style={{ fontSize: '12px' }}>
                        {unit.unit_type}
                      </span>
                    </td>
                    <td className="mono" style={{ fontSize: '12px' }}>
                      P.{unit.page_from}{unit.page_to !== unit.page_from ? `–${unit.page_to}` : ''}
                    </td>
                    <td style={{ maxWidth: '320px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                        {unit.text.replace(/\n/g, ' ')}
                      </span>
                    </td>
                    <td>
                      <StatusBadge status={unit.review_status} />
                    </td>
                    <td>
                      <span className="mono" style={{ color: 'var(--text-muted)', fontSize: '12px' }}>
                        false
                      </span>
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => setViewingUnit(unit)}
                      >
                        <Eye size={12} /> View
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Drawer: Full Text & Provenance Inspector */}
      <Drawer
        isOpen={!!viewingUnit}
        onClose={() => setViewingUnit(null)}
        title={viewingUnit ? `Content Unit #${viewingUnit.sequence_no}` : ''}
        subtitle={viewingUnit ? `Unit ID: ${viewingUnit.id}` : undefined}
      >
        {viewingUnit && (
          <div>
            <div className="kv-grid" style={{ marginBottom: '18px' }}>
              <span className="kv-key">Sequence #:</span>
              <span className="kv-val mono" style={{ fontWeight: 600 }}>#{viewingUnit.sequence_no}</span>

              <span className="kv-key">Version ID:</span>
              <span className="kv-val mono">{viewingUnit.resource_version_id}</span>

              <span className="kv-key">Unit Type:</span>
              <span className="kv-val mono">{viewingUnit.unit_type}</span>

              <span className="kv-key">Page Range:</span>
              <span className="kv-val mono">Page {viewingUnit.page_from} to {viewingUnit.page_to}</span>

              <span className="kv-key">Review Status:</span>
              <span className="kv-val">
                <StatusBadge status={viewingUnit.review_status} />
              </span>

              <span className="kv-key">Retrieval Eligible:</span>
              <span className="kv-val mono" style={{ color: 'var(--text-muted)' }}>
                false (Baseline specification)
              </span>

              <span className="kv-key">OCR Provenance FK:</span>
              <span className="kv-val mono">{viewingUnit.page_result_id}</span>
            </div>

            <div style={{ marginTop: '16px' }}>
              <label className="form-label">
                <span>Normalized Text Body (Unicode NFC)</span>
              </label>
              <textarea
                className="form-textarea mono"
                style={{
                  width: '100%',
                  height: '240px',
                  fontSize: '12px',
                  lineHeight: 1.6,
                  background: 'var(--bg-app)',
                }}
                readOnly
                value={viewingUnit.text}
              />
            </div>

            <div style={{ marginTop: '14px', fontSize: '11.5px', color: 'var(--text-muted)' }}>
              Provenance links directly to OCR page result <code>{viewingUnit.page_result_id}</code> for audit and verification.
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
};
