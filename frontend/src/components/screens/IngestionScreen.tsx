import React, { useState } from 'react';
import { useEtl } from '../../context/EtlContext';
import type { IngestionResult } from '../../types/etl';
import { StatusBadge } from '../common/StatusBadge';
import {
  UploadSimple,
  CheckCircle,
  Warning,
  ArrowRight,
} from '@phosphor-icons/react';

export const IngestionScreen: React.FC = () => {
  const {
    resources,
    versions,
    triggerIngestion,
    setActiveScreen,
    setSelectedResourceId,
    isLiveMode,
  } = useEtl();

  const [resourceId, setResourceId] = useState<string>('resource-1');
  const [bucket, setBucket] = useState<string>('input');
  const [objectKey, setObjectKey] = useState<string>('book_toan1_v3.pdf');
  const [uploadedBy, setUploadedBy] = useState<string>('operator-1');
  const [hashOverride, setHashOverride] = useState<string>('');
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [lastResult, setLastResult] = useState<IngestionResult | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Helper to prefill matching latest version hash (to demonstrate NO_CHANGE)
  const handleSimulateDuplicateHash = () => {
    const resVersions = versions
      .filter((v) => v.resource_id === resourceId)
      .sort((a, b) => b.version_number - a.version_number);
    if (resVersions.length > 0) {
      setHashOverride(resVersions[0].source_hash);
      setObjectKey(resVersions[0].storage_object_key);
    }
  };

  // Helper to prefill unique new hash
  const handleSimulateNewHash = () => {
    const randomHex = Array.from({ length: 64 }, () => Math.floor(Math.random() * 16).toString(16)).join('');
    setHashOverride(randomHex);
    setObjectKey(`book_${resourceId}_revised_${Date.now().toString().slice(-4)}.pdf`);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);
    setLastResult(null);

    if (!resourceId.trim() || resourceId.length > 30) {
      setErrorMessage('Resource ID is required (max 30 characters).');
      return;
    }
    if (!bucket.trim() || bucket.length > 255) {
      setErrorMessage('Storage Bucket is required (max 255 characters).');
      return;
    }
    if (!objectKey.trim() || objectKey.length > 500) {
      setErrorMessage('Storage Object Key is required (max 500 characters).');
      return;
    }
    if (uploadedBy && uploadedBy.length > 30) {
      setErrorMessage('Uploaded By exceeds 30 characters.');
      return;
    }

    setIsSubmitting(true);
    try {
      const result = await triggerIngestion(
        resourceId.trim(),
        bucket.trim(),
        objectKey.trim(),
        uploadedBy.trim() || undefined,
        hashOverride.trim() || undefined
      );
      setLastResult(result);
    } catch (err: any) {
      setErrorMessage(err.message || 'Ingestion execution error');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div>
      <div className="page-header">
        <div className="page-title-row">
          <div>
            <h1 className="page-title">Resource Ingestion</h1>
            <p className="page-description">
              Ingest PDF binaries into object storage, compute streaming SHA-256, and produce immutable physical versions (<code className="mono">resource_versions</code>).
            </p>
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: '20px' }}>
        {/* Form Container */}
        <div className="card">
          <div className="card-header">
            <div>
              <div className="card-title">Ingestion Parameters</div>
              <div className="card-subtitle">
                Target resource, storage coordinates, and hash verification
              </div>
            </div>
            {isLiveMode && (
              <span className="mono" style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                POST /admin/resources/&#123;id&#125;/versions/ingest
              </span>
            )}
          </div>

          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label className="form-label">
                <span>Resource ID <span className="required">*</span></span>
              </label>
              <select
                className="form-select mono"
                value={resourceId}
                onChange={(e) => setResourceId(e.target.value)}
                disabled={isSubmitting}
              >
                {resources.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.id} — {r.title}
                  </option>
                ))}
              </select>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: '12px' }}>
              <div className="form-group">
                <label className="form-label">
                  <span>Storage Bucket <span className="required">*</span></span>
                </label>
                <input
                  type="text"
                  className="form-input mono"
                  value={bucket}
                  onChange={(e) => setBucket(e.target.value)}
                  placeholder="input"
                  required
                  disabled={isSubmitting}
                />
              </div>

              <div className="form-group">
                <label className="form-label">
                  <span>Storage Object Key <span className="required">*</span></span>
                </label>
                <input
                  type="text"
                  className="form-input mono"
                  value={objectKey}
                  onChange={(e) => setObjectKey(e.target.value)}
                  placeholder="book_toan1_tap1.pdf"
                  required
                  disabled={isSubmitting}
                />
              </div>
            </div>

            <div className="form-group">
              <label className="form-label">
                <span>Uploaded By</span>
              </label>
              <input
                type="text"
                className="form-input"
                value={uploadedBy}
                onChange={(e) => setUploadedBy(e.target.value)}
                placeholder="operator-1"
                disabled={isSubmitting}
              />
            </div>

            {/* Test Simulation Controls */}
            <div
              style={{
                background: 'var(--bg-app)',
                border: '1px solid var(--border-default)',
                borderRadius: 'var(--radius-sm)',
                padding: '12px',
                marginBottom: '16px',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                  Hash Simulation (Optional Override)
                </span>
                <div style={{ display: 'flex', gap: '6px' }}>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={handleSimulateDuplicateHash}
                    title="Simulate identical hash to trigger NO_CHANGE"
                  >
                    Match Latest Hash
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={handleSimulateNewHash}
                    title="Generate unique random hash to create new version"
                  >
                    New Hash
                  </button>
                </div>
              </div>

              <input
                type="text"
                className="form-input mono"
                style={{ fontSize: '11px', width: '100%' }}
                value={hashOverride}
                onChange={(e) => setHashOverride(e.target.value)}
                placeholder="Leave blank for automatic file hash computation..."
                disabled={isSubmitting}
              />
            </div>

            {errorMessage && (
              <div className="notice-box warning" style={{ marginBottom: '14px' }}>
                <Warning size={16} color="var(--status-error)" style={{ flexShrink: 0 }} />
                <div className="notice-body">{errorMessage}</div>
              </div>
            )}

            <button
              type="submit"
              className="btn btn-primary"
              disabled={isSubmitting}
              style={{ width: '100%', height: '38px', justifyContent: 'center' }}
            >
              <UploadSimple size={15} />
              {isSubmitting ? 'Ingesting...' : 'Ingest Resource Version'}
            </button>
          </form>
        </div>

        {/* Feedback / Result Container */}
        <div>
          <div className="card" style={{ height: '100%' }}>
            <div className="card-header">
              <div>
                <div className="card-title">Execution Response</div>
                <div className="card-subtitle">
                  Result of hash comparison & physical version generation
                </div>
              </div>
            </div>

            {!lastResult ? (
              <div
                style={{
                  padding: '48px 20px',
                  textAlign: 'center',
                  color: 'var(--text-muted)',
                  fontSize: '13px',
                }}
              >
                No ingestion task submitted in this session.
              </div>
            ) : (
              <div>
                {lastResult.action === 'NO_CHANGE' ? (
                  <div
                    style={{
                      background: 'var(--bg-app)',
                      border: '1px solid var(--border-default)',
                      borderLeft: '3px solid var(--accent-primary)',
                      borderRadius: 'var(--radius-sm)',
                      padding: '12px 14px',
                      marginBottom: '16px',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' }}>
                      <CheckCircle size={16} color="var(--accent-primary)" />
                      <span style={{ fontWeight: 600, fontSize: '13px', color: 'var(--text-primary)' }}>
                        Action: NO_CHANGE
                      </span>
                    </div>
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                      SHA-256 matches latest version (<code>v{lastResult.version.version_number}</code>).
                      No new physical version was created.
                    </div>
                  </div>
                ) : (
                  <div
                    style={{
                      background: 'var(--bg-app)',
                      border: '1px solid var(--border-default)',
                      borderLeft: '3px solid var(--status-success)',
                      borderRadius: 'var(--radius-sm)',
                      padding: '12px 14px',
                      marginBottom: '16px',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' }}>
                      <CheckCircle size={16} color="var(--status-success)" />
                      <span style={{ fontWeight: 600, fontSize: '13px', color: 'var(--status-success)' }}>
                        Action: CREATED (v{lastResult.version.version_number})
                      </span>
                    </div>
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                      New physical version committed to <code>resource_versions</code>.
                    </div>
                  </div>
                )}

                <div className="kv-grid">
                  <span className="kv-key">Action:</span>
                  <span className="kv-val mono" style={{ fontWeight: 600 }}>
                    {lastResult.action}
                  </span>

                  <span className="kv-key">Version ID:</span>
                  <span className="kv-val mono">{lastResult.version.id}</span>

                  <span className="kv-key">Version #:</span>
                  <span className="kv-val mono" style={{ fontWeight: 600 }}>
                    v{lastResult.version.version_number}
                  </span>

                  <span className="kv-key">Status:</span>
                  <span className="kv-val">
                    <StatusBadge status={lastResult.version.lifecycle_status} />
                  </span>

                  <span className="kv-key">Object Key:</span>
                  <span className="kv-val mono">
                    {lastResult.version.storage_bucket}/{lastResult.version.storage_object_key}
                  </span>

                  <span className="kv-key">Source Hash:</span>
                  <span className="kv-val mono" style={{ fontSize: '11px', wordBreak: 'break-all' }}>
                    {lastResult.version.source_hash}
                  </span>

                  <span className="kv-key">Parent Version:</span>
                  <span className="kv-val mono">
                    {lastResult.version.parent_version_id || 'Root (v1)'}
                  </span>
                </div>

                <div style={{ marginTop: '18px', display: 'flex', gap: '8px' }}>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => {
                      setSelectedResourceId(lastResult.version.resource_id);
                      setActiveScreen('resource-detail', {
                        resourceId: lastResult.version.resource_id,
                        versionId: lastResult.version.id,
                      });
                    }}
                  >
                    View Version Details <ArrowRight size={12} />
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    onClick={() => setActiveScreen('ocr')}
                  >
                    Proceed to OCR
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
