import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { getIndexResources, getIndexVersions, startIndex } from '../../services/indexingApi'
import type { IndexRequestResult, ResourceOption, VersionOption } from '../../types/indexing'
import styles from './LibraryPages.module.css'

export function LibraryIndexingPage() {
    const [params, setParams] = useSearchParams()
    const resourceId = params.get('resourceId') ?? ''
    const versionId = params.get('versionId') ?? ''
    const [query, setQuery] = useState('')
    const [resources, setResources] = useState<ResourceOption[]>([])
    const [versions, setVersions] = useState<{ resourceId: string; items: VersionOption[] } | null>(null)
    const [error, setError] = useState('')
    const [refresh, setRefresh] = useState(0)
    const [loading, setLoading] = useState(false)

    useEffect(() => {
        const controller = new AbortController()
        const timer = window.setTimeout(() => {
            setLoading(true)
            void getIndexResources(query, controller.signal).then(data => {
                if (!controller.signal.aborted) { setResources(data.items); setError('') }
            }).catch(err => {
                if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Không tải được danh sách học liệu.')
            }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
        }, 250)
        return () => { window.clearTimeout(timer); controller.abort() }
    }, [query, refresh])

    useEffect(() => {
        if (!resourceId) return
        const controller = new AbortController()
        void getIndexVersions(resourceId, controller.signal).then(items => {
            if (!controller.signal.aborted) { setVersions({ resourceId, items }); setError('') }
        }).catch(err => {
            if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Không tải được phiên bản.')
        })
        return () => controller.abort()
    }, [resourceId, refresh])

    const currentVersions = versions?.resourceId === resourceId ? versions.items : []
    const selected = currentVersions.some(version => version.id === versionId && version.resource_id === resourceId)
    return <section className={styles.page}>
        <header className={styles.pageHeader}><h1>Phân đoạn và chỉ mục</h1></header>
        <div className={styles.card}>
            <div className={styles.form}>
                <div className={styles.field}><label htmlFor="index-query">Tìm học liệu</label>
                    <input id="index-query" value={query} onChange={event => setQuery(event.target.value)} />
                </div>
                <div className={styles.field}><label htmlFor="index-resource">Học liệu</label>
                    <select id="index-resource" value={resourceId} onChange={event => setParams({ resourceId: event.target.value })}>
                        <option value="">Chọn học liệu</option>
                        {resourceId && !resources.some(resource => resource.id === resourceId) && <option value={resourceId}>{resourceId}</option>}
                        {resources.map(resource => <option key={resource.id} value={resource.id}>{resource.title}</option>)}
                    </select>
                </div>
                <div className={styles.field}><label htmlFor="index-version">Phiên bản</label>
                    <select id="index-version" value={selected ? versionId : ''} disabled={!resourceId} onChange={event => setParams({ resourceId, versionId: event.target.value })}>
                        <option value="">Chọn phiên bản</option>
                        {currentVersions.map(version => <option key={version.id} value={version.id}>Phiên bản {version.version_number} — {version.id}</option>)}
                    </select>
                </div>
            </div>
            {loading && <p role="status">Đang tải học liệu...</p>}
            {error && <p className={styles.alert} role="alert">{error}</p>}
            <div className={styles.actions}><button className={styles.primaryButton} onClick={() => setRefresh(value => value + 1)}>Thử tải lại</button></div>
            {resourceId && <Link className={styles.backLink} to={`/library/${encodeURIComponent(resourceId)}`}>← Chi tiết học liệu</Link>}
        </div>
        {selected && <IndexWorkspace key={versionId} versionId={versionId} />}
    </section>
}

function IndexWorkspace({ versionId }: { versionId: string }) {
    const [result, setResult] = useState<IndexRequestResult | null>(null)
    const [error, setError] = useState('')
    const [busy, setBusy] = useState(false)
    const controller = useRef<AbortController | null>(null)
    useEffect(() => () => controller.current?.abort(), [])

    async function build() {
        controller.current?.abort()
        const current = new AbortController()
        controller.current = current
        setBusy(true)
        setError('')
        try {
            const data = await startIndex(versionId, current.signal)
            if (!current.signal.aborted) setResult(data)
        } catch (err) {
            if (!current.signal.aborted) setError(err instanceof Error ? err.message : 'Không thể xếp hàng lập chỉ mục.')
        } finally { if (!current.signal.aborted) setBusy(false) }
    }
    return <div className={styles.resultCard}>
        <h2>Xây dựng chỉ mục</h2>
        <p className={styles.resultValue}>{versionId}</p>
        {result && <p role="status">{result.index_status} · {result.index_run_id}</p>}
        {error && <p className={styles.alert} role="alert">{error}</p>}
        <div className={styles.actions}>
            <button className={styles.primaryButton} disabled={busy || result?.index_status === 'PENDING' || result?.index_status === 'RUNNING'} onClick={() => void build()}>
                {busy ? 'Đang gửi yêu cầu...' : 'Xây dựng chỉ mục'}
            </button>
        </div>
    </div>
}
