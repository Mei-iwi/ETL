import React from 'react';

interface StatusBadgeProps {
  status: string | null | undefined;
  className?: string;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status, className = '' }) => {
  if (!status) return null;

  const s = status.toUpperCase();
  let badgeClass = 'badge-pending';
  let displayLabel = status;

  switch (s) {
    case 'COMPLETED':
    case 'SUCCEEDED':
    case 'ACTIVE':
    case 'CONNECTED':
    case 'PUBLISHED':
      badgeClass = 'badge-completed';
      break;
    case 'RUNNING':
    case 'ENABLED (DEV MODE)':
      badgeClass = 'badge-running';
      break;
    case 'COMPLETED_WITH_ERRORS':
    case 'WARNING':
    case 'BLOCKED_SOURCE_MAPPING':
    case 'BLOCKED_OCR_RUNTIME':
    case 'BLOCKED':
      badgeClass = 'badge-warning';
      break;
    case 'FAILED':
    case 'ERROR':
    case '403 FORBIDDEN':
    case '503 UNAVAILABLE':
      badgeClass = 'badge-failed';
      break;
    case 'INGESTED':
      badgeClass = 'badge-ingested';
      break;
    case 'DISABLED':
    case 'DISABLED (DEFAULT)':
    case 'UNREVIEWED':
    case 'PENDING':
    default:
      badgeClass = 'badge-pending';
      break;
  }

  return (
    <span className={`badge ${badgeClass} ${className}`}>
      {displayLabel}
    </span>
  );
};
