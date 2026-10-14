import { useState } from 'react'
import { NavLink } from 'react-router'
import { SourceSyncFilesSection } from '../system/SystemMasterSyncPage'
import styles from '../system/SystemMasterSyncPage.module.css'

export function LibrarySyncErrorsPage({ retryable = true }: { retryable?: boolean }) {
    const [error, setError] = useState('')
    return (
        <section className={styles.page}>
            <h1>Lỗi đồng bộ</h1>
            <nav className={styles.errorTabs} aria-label="Nhóm lỗi đồng bộ">
                <NavLink to="/library/source-sync-errors/retry" end>Đồng bộ lại</NavLink>
                <NavLink to="/library/source-sync-errors/blocked" end>Lỗi đồng bộ</NavLink>
            </nav>
            {error && <p className={styles.error} role="alert">{error}</p>}
            <SourceSyncFilesSection key={String(retryable)} onError={setError} failedOnly retryableOnly={retryable} />
        </section>
    )
}
