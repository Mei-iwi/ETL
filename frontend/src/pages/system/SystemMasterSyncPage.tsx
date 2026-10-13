import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { syncMasterData } from '../../services/masterSyncApi'
import type {
    MasterSyncResponse,
    MasterSyncStream,
} from '../../types/masterSync'
import styles from './SystemMasterSyncPage.module.css'

const streamLabels: Record<string, string> = {
    resources: 'Học liệu',
    users: 'Người dùng',
    grades: 'Khối lớp',
    subjects: 'Môn học',
}

export function SystemMasterSyncPage() {
    const [stream, setStream] = useState<MasterSyncStream>('all')
    const [full, setFull] = useState(false)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')
    const [result, setResult] = useState<MasterSyncResponse | null>(null)
    const submittingRef = useRef(false)

    async function handleSubmit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault()
        if (submittingRef.current) return

        submittingRef.current = true
        setLoading(true)
        setError('')
        try {
            setResult(await syncMasterData({ stream, full }))
        } catch (caughtError) {
            setError(caughtError instanceof Error ? caughtError.message : 'Không thể đồng bộ dữ liệu')
        } finally {
            submittingRef.current = false
            setLoading(false)
        }
    }

    return (
        <section className={styles.page}>
            <h1>Đồng bộ dữ liệu</h1>
            <form className={`${styles.card} ${styles.form}`} onSubmit={handleSubmit}>
                <div className={styles.field}>
                    <label htmlFor="syncStream">Nhóm dữ liệu</label>
                    <select
                        id="syncStream"
                        value={stream}
                        onChange={(event) => setStream(event.target.value as MasterSyncStream)}
                        disabled={loading}
                    >
                        <option value="all">Tất cả</option>
                        <option value="resources">Học liệu</option>
                        <option value="users">Người dùng</option>
                        <option value="grades">Khối lớp</option>
                        <option value="subjects">Môn học</option>
                    </select>
                </div>
                <div className={styles.field}>
                    <label htmlFor="syncMode">Chế độ đồng bộ</label>
                    <select
                        id="syncMode"
                        value={full ? 'full' : 'incremental'}
                        onChange={(event) => setFull(event.target.value === 'full')}
                        disabled={loading}
                    >
                        <option value="incremental">Incremental</option>
                        <option value="full">Full</option>
                    </select>
                </div>
                <div className={styles.actions}>
                    <button className={styles.button} type="submit" disabled={loading}>
                        {loading ? 'Đang đồng bộ...' : 'Bắt đầu đồng bộ'}
                    </button>
                </div>
            </form>

            {error && <p className={styles.error} role="alert">{error}</p>}

            {result && (
                <div className={styles.results}>
                    {Object.entries(result).map(([name, data]) => (
                        <article className={styles.resultCard} key={name}>
                            <h2>{streamLabels[name] ?? name}</h2>
                            <dl className={styles.metrics}>
                                <div><dt>Đã đọc</dt><dd>{data.records_read}</dd></div>
                                <div><dt>Đã thêm</dt><dd>{data.inserted}</dd></div>
                                <div><dt>Đã cập nhật</dt><dd>{data.updated}</dd></div>
                                <div><dt>Bỏ qua</dt><dd>{data.skipped}</dd></div>
                                <div><dt>Lỗi</dt><dd>{data.failed}</dd></div>
                            </dl>
                        </article>
                    ))}
                </div>
            )}
        </section>
    )
}
