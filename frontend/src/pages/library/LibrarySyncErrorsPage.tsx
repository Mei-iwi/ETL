import { useState } from 'react'
import { SourceSyncFilesSection } from '../system/SystemMasterSyncPage'
import styles from '../system/SystemMasterSyncPage.module.css'

export function LibrarySyncErrorsPage() {
    const [error, setError] = useState('')
    return (
        <section className={styles.page}>
            <h1>Lỗi đồng bộ</h1>
            {error && <p className={styles.error} role="alert">{error}</p>}
            <SourceSyncFilesSection onError={setError} failedOnly />
        </section>
    )
}
