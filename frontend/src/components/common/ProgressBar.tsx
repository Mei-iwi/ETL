import React from 'react';

interface ProgressBarProps {
  completed: number;
  failed?: number;
  total: number;
  showLabels?: boolean;
}

export const ProgressBar: React.FC<ProgressBarProps> = ({
  completed,
  failed = 0,
  total,
  showLabels = true,
}) => {
  const safeTotal = total > 0 ? total : 1;
  const completedPercent = Math.min(100, Math.round((completed / safeTotal) * 100));
  const failedPercent = Math.min(100 - completedPercent, Math.round((failed / safeTotal) * 100));

  return (
    <div style={{ width: '100%' }}>
      {showLabels && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            fontSize: '11px',
            color: 'var(--text-muted)',
            marginBottom: '4px',
            fontFamily: 'var(--font-mono)',
          }}
        >
          <span>
            {completed}/{total} trang ({completedPercent}%)
          </span>
          {failed > 0 && (
            <span style={{ color: 'var(--status-error)' }}>
              {failed} lỗi
            </span>
          )}
        </div>
      )}
      <div className="progress-track">
        <div
          className="progress-fill"
          style={{ width: `${completedPercent}%` }}
        />
        {failed > 0 && (
          <div
            className="progress-fill failed"
            style={{ width: `${failedPercent}%` }}
          />
        )}
      </div>
    </div>
  );
};
