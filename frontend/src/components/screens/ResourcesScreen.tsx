import React, { useState } from 'react';
import { useEtl } from '../../context/EtlContext';
import { StatusBadge } from '../common/StatusBadge';
import { UploadSimple, ArrowRight, MagnifyingGlass } from '@phosphor-icons/react';

export const ResourcesScreen: React.FC = () => {
  const {
    resources,
    versions,
    setActiveScreen,
    setSelectedResourceId,
  } = useEtl();

  const [searchTerm, setSearchTerm] = useState('');
  const [providerFilter, setProviderFilter] = useState('all');

  const filteredResources = resources.filter((r) => {
    const matchesSearch =
      r.id.toLowerCase().includes(searchTerm.toLowerCase()) ||
      r.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
      r.provider_name.toLowerCase().includes(searchTerm.toLowerCase());

    const matchesProvider =
      providerFilter === 'all' || r.provider_id === providerFilter;

    return matchesSearch && matchesProvider;
  });

  const providers = Array.from(new Set(resources.map((r) => r.provider_id)));

  return (
    <div>
      {/* Header */}
      <div className="page-header">
        <div className="page-title-row">
          <div>
            <h1 className="page-title">Learning Resources (Catalog)</h1>
            <p className="page-description">
              Logical master entities synchronized into <code className="mono">master_learning_resources</code>. Physical files are tracked independently as Resource Versions.
            </p>
          </div>
          <div className="page-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setActiveScreen('ingest')}
            >
              <UploadSimple size={14} /> Ingest File
            </button>
          </div>
        </div>
      </div>

      {/* Filter Strip */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          marginBottom: '16px',
        }}
      >
        <div style={{ position: 'relative', width: '320px' }}>
          <input
            type="text"
            className="form-input"
            style={{ width: '100%', paddingLeft: '30px' }}
            placeholder="Filter by ID, title, or publisher..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
          <MagnifyingGlass
            size={14}
            style={{ position: 'absolute', left: '10px', top: '10px', color: 'var(--text-muted)' }}
          />
        </div>

        <select
          className="form-select"
          style={{ width: '220px' }}
          value={providerFilter}
          onChange={(e) => setProviderFilter(e.target.value)}
        >
          <option value="all">All Providers ({providers.length})</option>
          {providers.map((p) => {
            const item = resources.find((r) => r.provider_id === p);
            return (
              <option key={p} value={p}>
                {item ? item.provider_name : p}
              </option>
            );
          })}
        </select>

        <span style={{ marginLeft: 'auto', fontSize: '12px', color: 'var(--text-muted)' }}>
          Showing {filteredResources.length} of {resources.length} resources
        </span>
      </div>

      {/* Table-First Resources View */}
      <div className="table-wrapper">
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: '130px' }}>Resource ID</th>
              <th>Title</th>
              <th>Provider</th>
              <th>Type</th>
              <th>Subjects / Grades</th>
              <th>Versions</th>
              <th style={{ width: '110px' }}>Status</th>
              <th style={{ width: '90px' }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filteredResources.map((res) => {
              const resVersions = versions.filter((v) => v.resource_id === res.id);
              return (
                <tr key={res.id}>
                  <td>
                    <span className="mono" style={{ fontWeight: 600 }}>
                      {res.id}
                    </span>
                  </td>
                  <td>
                    <div style={{ fontWeight: 500 }}>{res.title}</div>
                  </td>
                  <td>
                    <div style={{ fontSize: '12.5px' }}>{res.provider_name}</div>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{res.provider_type}</div>
                  </td>
                  <td style={{ color: 'var(--text-secondary)' }}>
                    {res.resource_type_name_vi}
                  </td>
                  <td>
                    <span style={{ fontSize: '12px' }}>
                      {res.subjects.map((s) => s.name).join(', ')} — {res.grades.map((g) => g.name_vi).join(', ')}
                    </span>
                  </td>
                  <td>
                    <span className="hash-pill">
                      {resVersions.length} {resVersions.length === 1 ? 'version' : 'versions'}
                    </span>
                  </td>
                  <td>
                    <StatusBadge status={res.publication_status} />
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => {
                        setSelectedResourceId(res.id);
                        setActiveScreen('resource-detail', { resourceId: res.id });
                      }}
                    >
                      View <ArrowRight size={11} />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};
