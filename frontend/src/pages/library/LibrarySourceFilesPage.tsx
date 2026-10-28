import { useState } from 'react'
import { useSearchParams } from 'react-router'
import { SourceSyncFilesSection } from '../system/SystemMasterSyncPage'
import styles from '../system/SystemMasterSyncPage.module.css'

export function LibrarySourceFilesPage() {
    const [error, setError] = useState('')
    const [params, setParams] = useSearchParams()
    const filter = params.get('filter') ?? 'all'
    return <div>
        <nav className={styles.errorTabs} aria-label="Bộ lọc kho tệp">
            {Object.entries({ all: 'Tất cả', retry: 'Lỗi có thể retry', blocked: 'Bị chặn / cần xử lý', integrity: 'Chưa xác minh / lệch hash' }).map(([key, label]) =>
                <button type="button" key={key} className={styles.button} aria-pressed={filter === key}
                    onClick={() => setParams(key === 'all' ? {} : { filter: key })}>{label}</button>)}
        </nav>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <SourceSyncFilesSection key={filter} onError={setError} failedOnly={filter === 'retry' || filter === 'blocked'}
            retryableOnly={filter === 'retry' ? true : filter === 'blocked' ? false : undefined}
            integrityOnly={filter === 'integrity'} />
    </div>
}
