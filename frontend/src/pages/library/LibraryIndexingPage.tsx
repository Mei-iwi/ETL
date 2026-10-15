import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { getChunks, getIndexResources, getIndexStatus, getIndexVersions, IndexingApiError, reindex, startIndex } from '../../services/indexingApi'
import type { ChunksResult, IndexRequestResult, IndexStatusResult, ResourceOption, VersionOption } from '../../types/indexing'
import styles from './LibraryPages.module.css'

type IndexView = 'index' | 'chunks' | 'bags'
const views: { id: IndexView; label: string }[] = [
    { id: 'index', label: 'Chỉ mục' }, { id: 'chunks', label: 'Phân đoạn' }, { id: 'bags', label: 'ContentBag' },
]
const stateLabels: Record<IndexStatusResult['index_status'], string> = {
    CONTENT_NOT_READY: 'Chờ nội dung', NOT_STARTED: 'Chưa lập chỉ mục', PENDING: 'Đang chờ',
    RUNNING: 'Đang xử lý', READY: 'Sẵn sàng', FAILED: 'Thất bại', STALE: 'Cần cập nhật',
    BLOCKED_RUNTIME: 'Chưa sẵn sàng', BLOCKED_STRUCTURE: 'Nội dung không hợp lệ',
}

function statusTone(status: IndexStatusResult['index_status']) {
    if (status === 'READY') return 'success'
    if (['FAILED', 'BLOCKED_RUNTIME', 'BLOCKED_STRUCTURE'].includes(status)) return 'error'
    return status === 'NOT_STARTED' ? 'neutral' : 'warning'
}

function statusNotice(status: IndexStatusResult) {
    if (status.index_status === 'BLOCKED_RUNTIME') {
        if (status.error_code === 'BLOCKED_INDEX_SCHEMA') return 'Schema MongoDB chưa hợp lệ. Liên hệ quản trị viên.'
        if (status.error_code === 'INVALID_EMBEDDING') return 'Vector hoặc số chiều không hợp lệ.'
        return 'Model chưa sẵn sàng. Kiểm tra runtime.'
    }
    if (status.error_code === 'LEAF_KEYWORDS_UNAVAILABLE') return 'Chưa có từ khóa hợp lệ.'
    return {
        CONTENT_NOT_READY: 'Đang chờ nội dung.', NOT_STARTED: 'Chưa lập chỉ mục.',
        PENDING: 'Đang chờ xử lý.', RUNNING: 'Đang tạo chỉ mục.', READY: 'Chỉ mục sẵn sàng.',
        FAILED: 'Lập chỉ mục thất bại. Có thể tái lập.', STALE: 'Chỉ mục cần cập nhật.',
        BLOCKED_STRUCTURE: 'Nội dung không hợp lệ. Kiểm tra ContentUnit.',
    }[status.index_status]
}

function errorNotice(error: unknown, fallback: string) {
    if (error instanceof IndexingApiError) {
        if (error.code === 'BLOCKED_INDEX_SCHEMA') return 'Schema MongoDB chưa hợp lệ.'
        if (error.code === 'BLOCKED_EMBEDDING_RUNTIME') return 'Model chưa sẵn sàng.'
        if (error.code === 'INVALID_EMBEDDING') return 'Vector hoặc số chiều không hợp lệ.'
        if (error.code === 'INDEX_ALREADY_RUNNING') return 'Chỉ mục đang được xử lý.'
    }
    return error instanceof Error && error.message.length <= 140 ? error.message : fallback
}

export function LibraryIndexingPage() {
    const [params, setParams] = useSearchParams()
    const resourceId = params.get('resourceId') ?? ''
    const versionId = params.get('versionId') ?? ''
    const view: IndexView = params.get('view') === 'chunks' ? 'chunks' : params.get('view') === 'bags' ? 'bags' : 'index'
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
                if (!controller.signal.aborted) setError(errorNotice(err, 'Không tải được học liệu.'))
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
            if (!controller.signal.aborted) setError(errorNotice(err, 'Không tải được phiên bản.'))
        })
        return () => controller.abort()
    }, [resourceId, refresh])

    const currentVersions = versions?.resourceId === resourceId ? versions.items : []
    const selected = currentVersions.some(version => version.id === versionId && version.resource_id === resourceId)
    return <section className={styles.page}>
        <header className={styles.pageHeader}><h1>Phân đoạn và chỉ mục</h1></header>
        <nav className={styles.indexTabs} aria-label="Phân đoạn và chỉ mục">
            {views.map(item => {
                const search = new URLSearchParams(params)
                search.set('view', item.id)
                return <Link key={item.id} to={`?${search}`} aria-current={view === item.id ? 'page' : undefined}>{item.label}</Link>
            })}
        </nav>
        <div className={styles.card}>
            <div className={`${styles.form} ${styles.indexFilters}`}>
                <div className={styles.field}><label htmlFor="index-query">Tìm học liệu</label>
                    <input id="index-query" value={query} onChange={event => setQuery(event.target.value)} />
                </div>
                <div className={styles.field}><label htmlFor="index-resource">Học liệu</label>
                    <select id="index-resource" value={resourceId} onChange={event => setParams({ resourceId: event.target.value, view })}>
                        <option value="">Chọn học liệu</option>
                        {resourceId && !resources.some(resource => resource.id === resourceId) && <option value={resourceId}>{resourceId}</option>}
                        {resources.map(resource => <option key={resource.id} value={resource.id}>{resource.title}</option>)}
                    </select>
                </div>
                <div className={styles.field}><label htmlFor="index-version">Phiên bản</label>
                    <select id="index-version" value={selected ? versionId : ''} disabled={!resourceId} onChange={event => setParams({ resourceId, versionId: event.target.value, view })}>
                        <option value="">Chọn phiên bản</option>
                        {currentVersions.map(version => <option key={version.id} value={version.id}>Phiên bản {version.version_number}</option>)}
                    </select>
                </div>
            </div>
            {loading && <p className={styles.indexMessage} data-tone="warning" role="status">Đang tải học liệu...</p>}
            {error && <p className={styles.indexMessage} data-tone="error" role="alert">{error}</p>}
            <div className={`${styles.actions} ${styles.indexFilterActions}`}>
                <button className={styles.primaryButton} onClick={() => setRefresh(value => value + 1)}>Tải lại</button>
                {resourceId && <Link className={styles.backLink} to={`/library/${encodeURIComponent(resourceId)}`}>← Chi tiết học liệu</Link>}
            </div>
        </div>
        {selected && <IndexWorkspace key={versionId} versionId={versionId} view={view} />}
        {!selected && <p className={styles.indexMessage} data-tone="neutral">Chọn học liệu và phiên bản.</p>}
    </section>
}

function IndexWorkspace({ versionId, view }: { versionId: string; view: IndexView }) {
    const [result, setResult] = useState<IndexRequestResult | null>(null)
    const [error, setError] = useState('')
    const [busy, setBusy] = useState(false)
    const [status, setStatus] = useState<IndexStatusResult | null>(null)
    const [statusError, setStatusError] = useState('')
    const [refresh, setRefresh] = useState(0)
    const controller = useRef<AbortController | null>(null)
    const posting = useRef(false)
    const [confirming, setConfirming] = useState(false)
    useEffect(() => () => controller.current?.abort(), [])

    useEffect(() => {
        let active = true
        let timer: number | undefined
        let current: AbortController | null = null
        let failures = 0
        let reads = 0
        let inFlight = false
        let following = true
        function schedule(delay: number) {
            if (active && following && !document.hidden) timer = window.setTimeout(() => { timer = undefined; void poll() }, delay)
        }
        async function poll() {
            if (!active || document.hidden || inFlight || !following) return
            inFlight = true
            current = new AbortController()
            try {
                const data = await getIndexStatus(versionId, current.signal)
                if (!active) return
                setStatus(data)
                setStatusError('')
                failures = 0
                reads++
                const waitingContent = data.index_status === 'CONTENT_NOT_READY'
                following = waitingContent || data.index_status === 'PENDING' || data.index_status === 'RUNNING'
                if (following && reads >= (waitingContent ? 60 : 150)) {
                    following = false
                    setStatusError('Đã tạm dừng theo dõi. Tải lại trạng thái để tiếp tục.')
                }
                schedule(waitingContent ? Math.min(4000 * 2 ** Math.floor(reads / 10), 30000) : 4000)
            } catch (err) {
                if (!active) return
                setStatusError(errorNotice(err, 'Không tải được trạng thái.'))
                if ((!(err instanceof IndexingApiError) || err.status >= 500) && failures < 3) {
                    schedule(4000 * 2 ** failures++)
                } else {
                    following = false
                }
            } finally { inFlight = false }
        }
        function visibility() {
            window.clearTimeout(timer)
            timer = undefined
            if (!document.hidden) void poll()
        }
        document.addEventListener('visibilitychange', visibility)
        void poll()
        return () => { active = false; window.clearTimeout(timer); current?.abort(); document.removeEventListener('visibilitychange', visibility) }
    }, [versionId, refresh])

    async function build(force = false) {
        if (posting.current) return
        posting.current = true
        controller.current?.abort()
        const current = new AbortController()
        controller.current = current
        setBusy(true)
        setError('')
        try {
            const data = await (force ? reindex : startIndex)(versionId, current.signal)
            if (!current.signal.aborted) { setResult(data); setRefresh(value => value + 1) }
        } catch (err) {
            if (!current.signal.aborted) setError(errorNotice(err, 'Không gửi được yêu cầu.'))
        } finally { posting.current = false; if (!current.signal.aborted) setBusy(false) }
    }
    const running = status?.index_status === 'PENDING' || status?.index_status === 'RUNNING'
    const canBuild = status?.processing_status === 'CONTENT_READY' && status.index_status !== 'BLOCKED_RUNTIME'
        && status.index_status !== 'BLOCKED_STRUCTURE'
        && status.error_code !== 'LEAF_KEYWORDS_UNAVAILABLE'
        && !running && !statusError && !busy
    return <>
        <div className={styles.indexPanel} data-index-panel="index" hidden={view !== 'index'}>
            <section className={`${styles.resultCard} ${styles.indexOverview}`}>
                <h2>Trạng thái chỉ mục</h2>
                {!status && !statusError && <p className={styles.indexMessage} data-tone="warning" role="status">Đang tải trạng thái...</p>}
                {status && <>
                    <p className={styles.indexMessage} data-tone={statusTone(status.index_status)} role="status">{statusNotice(status)}</p>
                    <div className={styles.resultGrid}>
                        <p className={styles.resultItem}><span className={styles.resultLabel}>Nội dung</span>
                            <span className={styles.indexMessage} data-tone={status.processing_status === 'CONTENT_READY' ? 'success' : status.processing_status.endsWith('FAILED') ? 'error' : 'warning'}>{status.processing_status === 'CONTENT_READY' ? 'Sẵn sàng' : status.processing_status.endsWith('FAILED') ? 'Thất bại' : 'Đang xử lý'}</span></p>
                        <p className={styles.resultItem}><span className={styles.resultLabel}>Chỉ mục</span><span className={styles.indexMessage} data-tone={statusTone(status.index_status)}>{stateLabels[status.index_status]}</span></p>
                        <p className={styles.resultItem}><span className={styles.resultLabel}>Phương thức</span><span className={styles.resultValue}>{{ structured: 'Có cấu trúc', fallback: 'Không cấu trúc (Fallback)', mixed: 'Hỗn hợp', unavailable: 'Chưa xác định' }[status.representation_mode]}</span></p>
                        <p className={styles.resultItem}><span className={styles.resultLabel}>Tự động</span><span className={styles.indexMessage} data-tone={status.auto_enabled ? 'success' : 'neutral'}>{status.auto_enabled ? 'Bật' : 'Tắt'}</span></p>
                        <p className={styles.resultItem}><span className={styles.resultLabel}>Phân đoạn</span><span className={styles.resultValue}>{status.chunk_count}</span></p>
                        <p className={styles.resultItem}><span className={styles.resultLabel}>ContentBag</span><span className={styles.resultValue}>{status.bag_count}</span></p>
                        <p className={styles.resultItem}><span className={styles.resultLabel}>Vector</span><span className={styles.indexMessage} data-tone={status.vector_ready ? 'success' : running ? 'warning' : 'neutral'}>{status.vector_ready ? 'Vector sẵn sàng' : 'Chưa có vector'}</span></p>
                        <p className={styles.resultItem}><span className={styles.resultLabel}>Số chiều</span><span className={styles.resultValue}>{status.embedding_dimension ?? '—'}</span></p>
                    </div>
                    <details className={styles.indexDetails}><summary>Thông tin kỹ thuật</summary>
                        <dl><dt>Phiên bản</dt><dd>{versionId}</dd>
                            <dt>Model</dt><dd>{status.embedding_model ?? 'Chưa cấu hình'}</dd>
                            <dt>Lần chạy</dt><dd>{status.latest_run_id ?? '—'}</dd>
                            <dt>Trạng thái</dt><dd>{status.index_status}</dd>
                            {status.error_code && <><dt>Mã lỗi</dt><dd>{status.error_code}</dd></>}
                        </dl>
                    </details>
                </>}
                {result && result.index_run_id !== status?.latest_run_id && <p className={styles.indexMessage} data-tone={result.index_status === 'READY' ? 'success' : 'warning'} role="status">{result.index_status === 'READY' ? 'Chỉ mục sẵn sàng.' : 'Đã gửi yêu cầu.'}</p>}
                {statusError && <p className={styles.indexMessage} data-tone="error" role="alert">{statusError}</p>}
                {error && <p className={styles.indexMessage} data-tone="error" role="alert">{error}</p>}
                <div className={styles.actions}>
                    <button className={styles.primaryButton} disabled={!canBuild || status?.index_status === 'FAILED' || status?.index_status === 'READY'} onClick={() => void build()}>
                        {busy ? 'Đang gửi...' : 'Xây dựng chỉ mục'}
                    </button>
                    <button className={styles.secondaryButton} disabled={!canBuild} onClick={() => setConfirming(true)}>Tái lập chỉ mục</button>
                    <button className={styles.primaryButton} disabled={busy} onClick={() => setRefresh(value => value + 1)}>Tải lại trạng thái</button>
                </div>
            </section>
        </div>
        <div className={styles.indexPanel} data-index-panel="chunks" hidden={view !== 'chunks'}>
            <ChunkList key={`${status?.published_run_id ?? versionId}:${status?.processing_status}:${refresh}`} versionId={versionId} />
        </div>
        <div className={styles.indexPanel} data-index-panel="bags" hidden={view !== 'bags'}>
            <BagList key={versionId} status={status} loading={!status && !statusError} error={statusError} />
        </div>
        {confirming && <ReindexConfirmation onCancel={() => setConfirming(false)} onConfirm={() => { setConfirming(false); void build(true) }} />}
    </>
}

function BagList({ status, loading, error }: { status: IndexStatusResult | null; loading: boolean; error: string }) {
    const [offset, setOffset] = useState(0)
    const bags = status?.bags ?? []
    const pageSize = 6
    const currentOffset = offset < bags.length ? offset : 0
    return <section className={styles.resultCard}>
        <h2>ContentBag ({status?.bag_count ?? 0})</h2>
        {loading && <p className={styles.indexMessage} data-tone="warning" role="status">Đang tải ContentBag...</p>}
        {error && <p className={styles.indexMessage} data-tone="error" role="alert">{error}</p>}
        {!loading && !error && bags.length === 0 && <p className={styles.indexMessage} data-tone={status ? statusTone(status.index_status) : 'neutral'}>Chưa có ContentBag.</p>}
        <div className={styles.indexContentGrid}>
            {!error && bags.slice(currentOffset, currentOffset + pageSize).map((bag, index) => <article className={styles.contentItem} key={bag.id}>
                <h4>{bag.title ?? `ContentBag ${currentOffset + index + 1}`}</h4>
                <div className={styles.contentMeta}><span>{bag.unit_type}</span><span>{bag.embedding_dimension} chiều</span></div>
                <p>Từ khóa: {bag.keywords.join(', ')}</p>
                <p className={styles.indexMessage} data-tone={bag.vector_ready ? 'success' : 'warning'}>{bag.vector_ready ? 'Vector sẵn sàng' : 'Chưa có vector'}</p>
                <details><summary>Chi tiết</summary><p>ID: {bag.id}</p><p>Model: {bag.embedding_model}</p><p>{bag.embedding_text}</p></details>
            </article>)}
        </div>
        {bags.length > pageSize && <div className={styles.contentPagination} aria-label="Phân trang ContentBag">
            <button className={styles.primaryButton} disabled={currentOffset === 0} onClick={() => setOffset(Math.max(0, currentOffset - pageSize))}>Trước</button>
            <span>Trang {Math.floor(currentOffset / pageSize) + 1}/{Math.ceil(bags.length / pageSize)}</span>
            <button className={styles.primaryButton} disabled={currentOffset + pageSize >= bags.length} onClick={() => setOffset(currentOffset + pageSize)}>Tiếp</button>
        </div>}
    </section>
}

function ReindexConfirmation({ onConfirm, onCancel }: { onConfirm: () => void; onCancel: () => void }) {
    const dialog = useRef<HTMLDialogElement>(null)
    useEffect(() => {
        const element = dialog.current
        element?.showModal()
        return () => element?.close()
    }, [])
    return <dialog ref={dialog} className={styles.indexDialog} aria-labelledby="reindex-title" aria-describedby="reindex-description"
        onCancel={event => { event.preventDefault(); onCancel() }}>
        <h2 id="reindex-title">Tái lập chỉ mục?</h2>
        <p id="reindex-description">Vector hiện tại sẽ được thay thế.</p>
        <div className={`${styles.actions} ${styles.indexDialogActions}`}>
            <button className={styles.primaryButton} onClick={onConfirm}>Xác nhận</button>
            <button className={styles.secondaryButton} onClick={onCancel}>Hủy</button>
        </div>
    </dialog>
}

function ChunkList({ versionId }: { versionId: string }) {
    const [offset, setOffset] = useState(0)
    const [refresh, setRefresh] = useState(0)
    const [loaded, setLoaded] = useState<{ offset: number; data: ChunksResult } | null>(null)
    const [error, setError] = useState('')
    const [loading, setLoading] = useState(true)
    useEffect(() => {
        const controller = new AbortController()
        void getChunks(versionId, offset, controller.signal).then(data => {
            if (!controller.signal.aborted) { setLoaded({ offset, data }); setError('') }
        }).catch(err => {
            if (!controller.signal.aborted) setError(errorNotice(err, 'Không tải được phân đoạn.'))
        }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
        return () => controller.abort()
    }, [versionId, offset, refresh])
    const data = loaded?.offset === offset ? loaded.data : null
    function page(next: number) { setLoading(true); setError(''); setOffset(next) }
    return <section className={styles.resultCard}>
        <h2>Danh sách Chunk{data ? ` (${data.total})` : ''}</h2>
        {loading && <p className={styles.indexMessage} data-tone="warning" role="status">Đang tải phân đoạn...</p>}
        {error && <p className={styles.indexMessage} data-tone="error" role="alert">{error}</p>}
        {!loading && !error && data?.total === 0 && <p className={styles.indexMessage} data-tone="neutral">Chưa có phân đoạn.</p>}
        <div className={styles.indexContentGrid}>
            {!error && data?.items.map(chunk => <article className={styles.contentItem} key={chunk.id}>
                <h4>Chunk {chunk.chunk_sequence}</h4>
                <div className={styles.contentMeta}><span>Trang: {chunk.page_from ?? '—'}{chunk.page_to && chunk.page_to !== chunk.page_from ? `–${chunk.page_to}` : ''}</span></div>
                <p>Từ khóa: {chunk.leaf_keywords.join(', ') || 'Chưa có'}</p>
                <details><summary>Xem nội dung</summary><p>{chunk.text}</p>
                    <p>ContentUnit: {chunk.content_unit_id}</p><p>Ký tự: {chunk.char_start}–{chunk.char_end}</p>
                </details>
            </article>)}
        </div>
        <div className={styles.contentPagination}>
            <button className={styles.primaryButton} disabled={loading || offset === 0} onClick={() => page(Math.max(0, offset - 10))}>Trước</button>
            <span>Trang {Math.floor(offset / 10) + 1}/{Math.max(1, Math.ceil((data?.total ?? 0) / 10))}</span>
            <button className={styles.primaryButton} disabled={loading || !data || offset + 10 >= data.total} onClick={() => page(offset + 10)}>Tiếp</button>
            <button className={styles.primaryButton} disabled={loading} onClick={() => { setLoading(true); setRefresh(value => value + 1) }}>Tải lại Chunk</button>
        </div>
    </section>
}
