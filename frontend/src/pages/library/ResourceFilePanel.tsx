import { useEffect, useState } from 'react'
import { FileText, Eye, X, Download, ExternalLink, RotateCw, Check, LoaderCircle } from 'lucide-react'
import styles from './LibraryResourceDetailPage.module.css'
import { SearchContext } from '../search/SearchContext'
import { canOpenFile, embeddedSourceUrl, fileResponseError, trustedViewerHref } from './fileReadiness'
import type { FileIndexStatus, SourceOpen } from './fileReadiness'
import { MEDIA_PREVIEW_MAX_BYTES, readPreviewBlob } from './previewBody'
import { SpreadsheetPreview } from './SpreadsheetPreview'
import { parseSpreadsheetPreview } from './spreadsheetData'
import type { SpreadsheetData } from './spreadsheetData'
import { MediaPlayer } from './MediaFilePreview'
import { mediaPreviewType } from './libraryFileFormats'
import { startVisiblePolling } from '../system/visiblePolling'

type Version = { id: string; version_number: number; created_at: string }

type FilePanelProps = {
    resourceId: string; versionId: string | null; contextId: string | null; sourceOwned?: boolean
    contextEvidence?: string | null
}
export function ResourceFilePanel(props: FilePanelProps) {
    return <VersionFilePanel key={JSON.stringify([props.resourceId, props.versionId])} {...props} />
}
function VersionFilePanel({ resourceId, versionId, contextId, contextEvidence, sourceOwned = false }: FilePanelProps) {
    const [versions, setVersions] = useState<Version[]>([])
    const [selected, setSelected] = useState(versionId || '')
    const [error, setError] = useState('')
    const [loading, setLoading] = useState(true)
    const [open, setOpen] = useState(false)
    const [readiness, setReadiness] = useState<{ id: string; status: FileIndexStatus } | null>(null)
    const [statusError, setStatusError] = useState<{ id: string; message: string } | null>(null)
    const [attempt, setAttempt] = useState(0)
    const [sourceOpen, setSourceOpen] = useState<{ id: string; attempt: number; value: SourceOpen } | null>(null)
    const [sourceOpenError, setSourceOpenError] = useState<{ id: string; attempt: number; message: string } | null>(null)
    const [sourceViewer, setSourceViewer] = useState<{ id: string; url: string } | null>(null)
    useEffect(() => {
        const controller = new AbortController()
        void fetch(`/api/v1/resources/${encodeURIComponent(resourceId)}/versions`, { signal: controller.signal })
            .then(async response => {
                if (!response.ok) throw new Error('Không tải được danh sách tệp.')
                const rows: Version[] = await response.json()
                if (controller.signal.aborted) return
                rows.sort((a, b) => b.version_number - a.version_number)
                setVersions(rows)
                if (!versionId) setSelected(rows[0]?.id || '')
                else if (!rows.some(row => row.id === versionId)) setError('Không tìm thấy phiên bản được chọn.')
            }).catch(() => { if (!controller.signal.aborted) setError('Không tải được danh sách tệp.') })
            .finally(() => { if (!controller.signal.aborted) setLoading(false) })
        return () => controller.abort()
    }, [resourceId, versionId])
    const valid = versions.some(row => row.id === selected)
    const url = `/api/v1/resource-versions/${encodeURIComponent(selected)}/file`
    const status = readiness?.id === selected ? readiness.status : null
    const metadataOnly = status?.metadata_only === true
    const ready = !sourceOwned && valid && !metadataOnly && canOpenFile(status) && statusError?.id !== selected
    const currentSourceOpen = sourceOpen?.id === selected && sourceOpen.attempt === attempt ? sourceOpen.value : null
    const currentSourceError = sourceOpenError?.id === selected && sourceOpenError.attempt === attempt ? sourceOpenError.message : null
    const sourcePending = sourceOwned && valid && !currentSourceOpen && !currentSourceError
    const sourceFiles = currentSourceOpen?.status === 'EXTERNAL_VIEWER_AVAILABLE'
        ? currentSourceOpen.files.filter(file => file.access_status === 'ALLOWED' && trustedViewerHref(file.viewer_url)) : []
    const activeSourceFile = valid && sourceViewer?.id === selected
        ? sourceFiles.find(file => embeddedSourceUrl(file) === sourceViewer.url) : null
    useEffect(() => {
        if (!sourceOwned || !selected) { setSourceOpen(null); return }
        const controller = new AbortController()
        void fetch(`/api/v1/resources/${encodeURIComponent(resourceId)}/open?version_id=${encodeURIComponent(selected)}`, {
            signal: AbortSignal.any([controller.signal, AbortSignal.timeout(60_000)]),
        })
            .then(async response => {
                if (!response.ok) throw new Error(await fileResponseError(response))
                const value: SourceOpen = await response.json()
                if (value.version_id !== selected) throw new Error('Link xem tệp không khớp phiên bản được chọn. Hãy kiểm tra lại.')
                if (!controller.signal.aborted) {
                    setSourceOpen({ id: selected, attempt, value })
                    setSourceOpenError(null)
                }
            }).catch(reason => { if (!controller.signal.aborted) {
                setSourceOpen(null)
                setSourceOpenError({ id: selected, attempt, message: reason instanceof Error && reason.name === 'TimeoutError'
                    ? 'Quá thời gian tải link xem tệp. Hãy kiểm tra lại.'
                    : reason instanceof Error ? reason.message : 'Không thể mở tệp nguồn.' })
            } })
        return () => controller.abort()
    }, [resourceId, selected, sourceOwned, attempt])
    useEffect(() => {
        if (!valid) return
        let pending = false
        let failures = 0
        const polling = startVisiblePolling(async signal => {
            await Promise.resolve()
            if (signal.aborted) return
            try {
                const response = await fetch(`/api/v1/resource-versions/${encodeURIComponent(selected)}/index-status?include_bags=false`, { signal })
                if (!response.ok) throw new Error(await fileResponseError(response))
                const data: FileIndexStatus = await response.json()
                if (!signal.aborted) {
                    setReadiness({ id: selected, status: data }); setStatusError(null)
                    failures = 0
                    pending = ['CONTENT_NOT_READY', 'PENDING', 'RUNNING'].includes(data.index_status)
                }
            } catch (reason) {
                if (!signal.aborted) {
                    pending = ++failures <= 3
                    setStatusError({ id: selected, message: reason instanceof Error ? reason.message : 'Không tải được trạng thái tệp.' })
                }
            }
        // An open preview still needs live readiness checks; a closed READY file
        // is revalidated on visibility, manual retry or the next open.
        }, () => pending || (open && failures === 0), document, { busy: 5000, idle: null })
        return () => polling.stop()
    }, [selected, valid, attempt, open])
    return <section className={`${styles.panel} ${styles.filePanel}`} aria-labelledby="resource-file-title">
        <div className={styles.fileHeading}>
            <h2 id="resource-file-title"><FileText size={21} aria-hidden="true" />Tệp học liệu</h2>
            {!loading && versions.length > 0 && <span className={styles.fileCount}>{versions.length} phiên bản</span>}
        </div>
        {error && <p role="alert" className={styles.error}>{error}</p>}
        {loading ? <p className={styles.message}>Đang tải danh sách tệp…</p> :
            !error && !versions.length && <p className={styles.fileEmpty}>{sourceOwned
                ? 'Chưa nhập tệp từ nguồn. Kiểm tra Đồng bộ dữ liệu nguồn.'
                : 'Chưa có tệp học liệu.'}</p>}
        {versions.length > 0 && <div className={styles.fileToolbar}>
            <label>Phiên bản <select aria-label="Phiên bản tệp" value={selected}
                onChange={event => { setSelected(event.target.value); setOpen(false); setSourceViewer(null) }}>
                {versions.map(row => <option value={row.id} key={row.id}>Phiên bản {row.version_number} · {new Date(row.created_at).toLocaleDateString('vi-VN')}</option>)}
            </select></label>
            <div className={styles.fileActions}>
            {!sourceOwned && !metadataOnly && <button className={styles.historyLink} disabled={!ready} aria-expanded={open && ready}
                onClick={() => setOpen(value => !value)}>
                {open ? <X size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}{open ? 'Đóng tệp' : 'Xem tệp'}</button>}
            {ready && <a className={styles.secondaryButton} href={`${url}?download=true`} download><Download size={15} aria-hidden="true" />Tải tệp</a>}
            {sourceOwned && valid && sourceFiles.length === 0 && <button className={styles.historyLink} type="button" disabled
                aria-busy={sourcePending} title={sourcePending ? 'Đang lấy link xem tệp từ nguồn' : 'Chưa có link xem tệp được xác minh từ nguồn'}>
                {sourcePending ? <LoaderCircle size={15} className={styles.loadingSpinner} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}
                {sourcePending ? 'Đang tải…' : 'Xem tệp'}</button>}
            {sourceOwned && sourceFiles.map(file => {
                    const href = trustedViewerHref(file.viewer_url)
                    const embeddedHref = embeddedSourceUrl(file)
                    return href ? <span className={styles.fileActions} key={file.source_file_id}>
                        <button className={styles.historyLink} type="button" disabled={!embeddedHref}
                            aria-controls="source-file-preview"
                            aria-expanded={Boolean(activeSourceFile && sourceViewer?.url === embeddedHref)}
                            onClick={() => { if (embeddedHref) setSourceViewer(current => current?.id === selected && current.url === embeddedHref
                                ? null : { id: selected, url: embeddedHref }) }}>
                            <Eye size={15} aria-hidden="true" />{activeSourceFile && sourceViewer?.url === embeddedHref
                                ? 'Đóng tệp' : 'Xem tệp'}
                        </button>
                        <a className={styles.secondaryButton} href={href} target="_blank" rel="noopener noreferrer" title={file.file_name || undefined}>
                            <ExternalLink size={15} aria-hidden="true" />Xem tệp bên ngoài
                        </a>
                    </span> : null
                })}
            {valid && <button className={styles.secondaryButton} disabled={sourcePending}
                onClick={() => { setSourceViewer(null); setAttempt(n => n + 1) }}><RotateCw size={14} aria-hidden="true" />Kiểm tra lại</button>}
            </div>
        </div>}
        {valid && <div role="status" className={styles.fileStatus}>
            <span className={styles.ready}><Check size={14} aria-hidden="true" />Đã tiếp nhận</span>
            <>
                <span className={status?.processing_status === 'CONTENT_READY' ? styles.ready : styles.pending}>
                    Nội dung {status?.processing_status === 'CONTENT_READY' ? 'sẵn sàng' : 'chưa sẵn sàng'}</span>
                <span className={status?.index_status === 'READY' ? styles.ready : styles.pending}>
                    Chỉ mục {status?.index_status === 'READY' ? 'sẵn sàng' : 'chưa sẵn sàng'}</span>
            </>
            {sourceOwned && currentSourceOpen?.status === 'METADATA_ONLY'
                && <span className={styles.pending}>Chỉ có metadata; kho nguồn chưa cấp quyền hoặc link xem tệp</span>}
            {metadataOnly && <span className={styles.pending}>Nội dung đang chờ xử lý</span>}
        </div>}
        {currentSourceError && <p role="alert" className={styles.error}>{currentSourceError}</p>}
        {statusError?.id === selected && <p role="alert" className={styles.error}>{statusError.message}</p>}
        {sourceOwned && valid && currentSourceOpen && currentSourceOpen.status !== 'EXTERNAL_VIEWER_AVAILABLE'
            && <p role="status" className={styles.message}>{({
                SOURCE_AUTH_REQUIRED: 'Cần đăng nhập tại kho nguồn để mở học liệu.',
                METADATA_ONLY: 'Nguồn chỉ cung cấp metadata hoặc chưa có viewer được xác minh.',
                SOURCE_UNAVAILABLE: 'Kho nguồn tạm thời không khả dụng.',
                SOURCE_OPEN_UNSUPPORTED: 'Kho nguồn chưa hỗ trợ mở học liệu từ hệ thống này.',
                VERSION_STALE: 'Phiên bản nguồn đã thay đổi; hãy đồng bộ lại trước khi mở.',
                ACCESS_DENIED: 'Kho nguồn từ chối quyền truy cập học liệu.',
            } as Record<SourceOpen['status'], string>)[currentSourceOpen.status]}</p>}
        {sourceOwned && activeSourceFile && sourceViewer && <div className={styles.filePreview}>
            <p className={styles.subtitle}>{activeSourceFile.file_name || 'Nội dung học liệu'}</p>
            <iframe id="source-file-preview" title="Xem tệp từ nguồn" src={sourceViewer.url} loading="lazy"
                referrerPolicy="strict-origin-when-cross-origin" allowFullScreen />
        </div>}
        {open && ready && <FilePreview key={selected} url={url} />}
        {contextId && selected === versionId && <div className={styles.fileContext}><SearchContext id={contextId} evidence={contextEvidence} /></div>}
    </section>
}

function FilePreview({ url }: { url: string }) {
    const [file, setFile] = useState<{ url: string; type: string; size: number; text: string; grid: SpreadsheetData | null } | null>(null)
    const [error, setError] = useState('')
    useEffect(() => {
        const controller = new AbortController()
        let objectUrl = ''
        void fetch(`${url}?view=preview`, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(240000)]) })
            .then(async response => {
                if (!response.ok) throw new Error(await fileResponseError(response))
                const blob = await readPreviewBlob(response, controller.signal,
                    mediaPreviewType(response.headers.get('Content-Type') ?? '') ? MEDIA_PREVIEW_MAX_BYTES : undefined)
                const text = blob.type.startsWith('text/') ? await blob.slice(0, 200000).text() : ''
                const grid = blob.type.split(';')[0] === 'application/json' ? parseSpreadsheetPreview(await blob.text().then(JSON.parse)) : null
                if (controller.signal.aborted) return
                objectUrl = URL.createObjectURL(blob)
                setFile({ url: objectUrl, type: blob.type.split(';')[0], size: blob.size, text, grid })
            }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Không tải được tệp.') })
        return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
    }, [url])
    if (error) return <p role="alert" className={styles.error}>{error}</p>
    if (!file) return <p className={styles.message}>Đang tải tệp…</p>
    const mediaKind = mediaPreviewType(file.type)
    return <div className={styles.filePreview}>
        <p className={styles.subtitle}>{mediaKind === 'audio' ? 'Âm thanh MP3' : mediaKind === 'video' ? 'Video MP4' : file.grid ? 'Bảng tính' : file.type === 'application/pdf' ? 'Bản xem PDF' : file.type.startsWith('image/') ? 'Hình ảnh' :
            file.type.startsWith('text/') ? 'Văn bản' : 'Tệp học liệu'} · {(file.size / 1024 / 1024).toFixed(2)} MB · Tải xuống giữ định dạng gốc</p>
        {mediaKind ? <MediaPlayer key={file.url} url={file.url} kind={mediaKind} /> : file.grid ? <SpreadsheetPreview data={file.grid} /> : file.type === 'application/pdf' ? <iframe title="Xem tệp PDF" src={file.url} /> :
            file.type.startsWith('image/') ? <img src={file.url} alt="Nội dung tệp học liệu" /> :
                file.type.startsWith('text/') ? <pre>{file.text}{file.size > 200000 && '\n… Tải tệp để xem toàn bộ.'}</pre> :
                    <p className={styles.message}>Định dạng này chưa hỗ trợ xem trực tiếp tại đây. Hãy mở tại nguồn hoặc tải tệp để xem đúng định dạng.</p>}
    </div>
}
