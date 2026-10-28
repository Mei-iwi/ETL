import { Navigate } from 'react-router'

// Preserve imports and bookmarks while all file filters share one active view.
export function LibrarySyncErrorsPage({ retryable = true }: { retryable?: boolean }) {
    return <Navigate to={`/library/source-sync/files?filter=${retryable ? 'retry' : 'blocked'}`} replace />
}
