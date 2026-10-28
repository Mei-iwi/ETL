import { useState } from 'react'
import { SourceSyncFilesSection } from '../system/SystemMasterSyncPage'
import styles from '../system/SystemMasterSyncPage.module.css'

export function LibrarySourceFilesPage() {
    const [error, setError] = useState('')
    return <div>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <SourceSyncFilesSection onError={setError} />
    </div>
}
