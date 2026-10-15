import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { getChunks, getIndexResources, getIndexStatus, getIndexVersions, IndexingApiError, reindex, startIndex } from '../../services/indexingApi'
import type { ChunksResult, IndexRequestResult, IndexStatusResult, ResourceOption, VersionOption } from '../../types/indexing'
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
                    setStatusError('Đã tạm dừng theo dõi tự động. Chọn Thử tải lại trạng thái để tiếp tục; nếu đang chờ chỉ mục, kiểm tra worker OCR.')
                }
                schedule(waitingContent ? Math.min(4000 * 2 ** Math.floor(reads / 10), 30000) : 4000)
            } catch (err) {
                if (!active) return
                setStatusError(err instanceof IndexingApiError ? err.message : 'Không cập nhật được trạng thái chỉ mục.')
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
            if (!current.signal.aborted) setError(err instanceof Error ? err.message : 'Không thể xếp hàng lập chỉ mục.')
        } finally { posting.current = false; if (!current.signal.aborted) setBusy(false) }
    }
    const running = status?.index_status === 'PENDING' || status?.index_status === 'RUNNING'
    const canBuild = status?.processing_status === 'CONTENT_READY' && status.index_status !== 'BLOCKED_RUNTIME'
        && status.index_status !== 'BLOCKED_STRUCTURE'
        && status.error_code !== 'LEAF_KEYWORDS_UNAVAILABLE'
        && !running && !statusError && !busy
    const guidance = status?.index_status === 'BLOCKED_STRUCTURE'
        ? 'Kiểm tra parent, vòng lặp và đơn vị lá của nguồn. Tài liệu không có Heading được tự xử lý Fallback nếu có leaf hợp lệ; vẫn có thể xem Chunk bên dưới.'
        : status?.error_code === 'BLOCKED_INDEX_SCHEMA'
            ? 'Schema MongoDB chưa tương thích với ContentBag (validator, index hoặc dimension). Quản trị viên cần kiểm tra cấu hình môi trường Dev. Giao diện không tự sửa database.'
            : status?.error_code === 'INVALID_EMBEDDING'
                ? 'Kiểm tra model, revision và số chiều giữa runtime, cấu hình và validator MongoDB.'
                : status?.index_status === 'BLOCKED_RUNTIME'
                    ? 'Quản trị viên cần xác nhận model local, revision và runtime embedding của worker.'
                    : status?.index_status === 'CONTENT_NOT_READY'
                        ? 'Đang đợi OCR/chuẩn hóa. Trang sẽ tự kiểm tra khi đang được mở; có thể tải lại trạng thái.'
                        : running ? 'Đang chờ hoặc xử lý INDEX_BUILD. Nếu chờ lâu, kiểm tra worker OCR đang hoạt động.' : ''
    return <><div className={styles.resultCard}>
        <h2>Trạng thái chỉ mục</h2>
        <p className={styles.resultValue}>{versionId}</p>
        {!status && !statusError && <p role="status">Đang tải trạng thái...</p>}
        {status && <>
            <p className={styles.statusMessage} role="status">{status.message}</p>
            {status.auto_enabled && <p>Chỉ mục sẽ tự được tạo sau CONTENT_READY theo phương thức phù hợp: Có cấu trúc, Fallback hoặc Hỗn hợp.</p>}
            {!status.auto_enabled && status.index_status === 'NOT_STARTED' && <p>Có thể xây dựng chỉ mục một lần cho phiên bản hiện có; phương thức được chọn tự động.</p>}
            <div className={styles.resultGrid}>
                <p className={styles.resultItem}><span className={styles.resultLabel}>Trích xuất / OCR</span><span className={styles.resultValue}>{status.processing_status}</span></p>
                <p className={styles.resultItem}><span className={styles.resultLabel}>Chỉ mục</span><span className={styles.resultValue}>{status.index_status}</span></p>
                <p className={styles.resultItem}><span className={styles.resultLabel}>Phương thức</span><span className={styles.resultValue}>{{ structured: 'Có cấu trúc', fallback: 'Không cấu trúc (Fallback)', mixed: 'Hỗn hợp', unavailable: 'Chưa xác định' }[status.representation_mode]}</span></p>
                <p className={styles.resultItem}><span className={styles.resultLabel}>Tự động sau chuẩn hóa</span><span className={styles.resultValue}>{status.auto_enabled ? 'Bật' : 'Tắt'}</span></p>
                <p className={styles.resultItem}><span className={styles.resultLabel}>Số Chunk</span><span className={styles.resultValue}>{status.chunk_count}</span></p>
                <p className={styles.resultItem}><span className={styles.resultLabel}>ContentBag / Vector</span><span className={styles.resultValue}>{status.bag_count} Bag · {status.vector_ready ? 'Vector sẵn sàng' : 'Chưa có vector'}</span></p>
                <p className={styles.resultItem}><span className={styles.resultLabel}>Model / số chiều</span><span className={styles.resultValue}>{status.embedding_model ?? 'Chưa cấu hình'} · {status.embedding_dimension ?? '—'}</span></p>
                <p className={styles.resultItem}><span className={styles.resultLabel}>Lần chạy gần nhất</span><span className={styles.resultValue}>{status.latest_run_id ?? '—'}</span></p>
                {status.error_code && <p className={styles.resultItem}><span className={styles.resultLabel}>Mã lỗi</span><span className={styles.resultValue}>{status.error_code}</span></p>}
            </div>
        </>}
        {result && <p role="status">Yêu cầu: {result.index_run_id}</p>}
        {statusError && <p className={styles.alert} role="alert">{statusError}</p>}
        {error && <p className={styles.alert} role="alert">{error}</p>}
        {guidance && <p id="index-action-help">{guidance}</p>}
        <div className={styles.actions}>
            <button className={styles.primaryButton} aria-describedby={guidance ? 'index-action-help' : undefined} disabled={!canBuild || status?.index_status === 'FAILED' || status?.index_status === 'READY'} onClick={() => void build()}>
                {busy ? 'Đang gửi yêu cầu...' : 'Xây dựng chỉ mục'}
            </button>
            <button className={styles.secondaryButton} aria-describedby={guidance ? 'index-action-help' : undefined} disabled={!canBuild} onClick={() => setConfirming(true)}>Tái lập chỉ mục</button>
            <button className={styles.primaryButton} disabled={busy} onClick={() => setRefresh(value => value + 1)}>Thử tải lại trạng thái</button>
        </div>
        {confirming && <ReindexConfirmation onCancel={() => setConfirming(false)} onConfirm={() => { setConfirming(false); void build(true) }} />}
    </div><ChunkList key={`${status?.published_run_id ?? versionId}:${status?.processing_status}:${refresh}`} versionId={versionId} />
        <section className={styles.resultCard}>
            <h2>ContentBag ({status?.bag_count ?? 0})</h2>
            {status?.bags.map(bag => <article className={styles.contentItem} key={bag.id}>
                <h4>{bag.title ?? `Đơn vị nội dung ${bag.id}`} · {bag.unit_type}</h4>
                <p>BoW: {bag.keywords.join(', ')}</p>
                <details><summary>Xem chuỗi embedding</summary><p>{bag.embedding_text}</p></details>
                <p>Vector ContentBag: {bag.vector_ready ? 'Sẵn sàng' : 'Chưa có'}</p>
                <p>Model: {bag.embedding_model} · {bag.embedding_dimension} chiều</p>
            </article>)}
            {status?.bag_count === 0 && <p>{status.message}</p>}
        </section></>
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
        <p id="reindex-description">Tính lại keywords, ContentBag và embedding; không chạy lại OCR. Lịch sử chạy giữ ở PostgreSQL; vector Bag hiện tại có thể bị thay thế.</p>
        <div className={styles.actions}>
            <button className={styles.primaryButton} onClick={onCancel}>Hủy</button>
            <button className={styles.secondaryButton} onClick={onConfirm}>Xác nhận</button>
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
            if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Không tải được Chunk.')
        }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
        return () => controller.abort()
    }, [versionId, offset, refresh])
    const data = loaded?.offset === offset ? loaded.data : null
    function page(next: number) { setLoading(true); setError(''); setOffset(next) }
    return <section className={styles.resultCard}>
        <h2>Danh sách Chunk{data ? ` (${data.total})` : ''}</h2>
        <p>Đoạn xem trước, không lưu collection Chunk.</p>
        {loading && <p role="status">Đang tải Chunk...</p>}
        {error && <p className={styles.alert} role="alert">{error}</p>}
        {!loading && !error && data?.total === 0 && <p>Chưa có nội dung chuẩn hóa để xem trước.</p>}
        <div className={styles.contentList}>
            {!error && data?.items.map(chunk => <article className={styles.contentItem} key={chunk.id}>
                <h4>Chunk {chunk.chunk_sequence}</h4>
                <div className={styles.contentMeta}><span>Trang: {chunk.page_from ?? '—'}{chunk.page_to && chunk.page_to !== chunk.page_from ? `–${chunk.page_to}` : ''}</span></div>
                <p>Content Unit: {chunk.content_unit_id}</p>
                <p>Vị trí ký tự: {chunk.char_start}–{chunk.char_end}</p>
                <details><summary>Xem nội dung Chunk</summary><p>{chunk.text}</p></details>
                <p>Từ khóa ContentUnit lá: {chunk.leaf_keywords.join(', ') || 'Chưa có'}</p>
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
