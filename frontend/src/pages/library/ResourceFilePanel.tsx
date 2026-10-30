import { useEffect, useState } from 'react'
import { FileText, Eye, X, Download, ExternalLink, RotateCw, Check } from 'lucide-react'
import styles from './LibraryResourceDetailPage.module.css'
import { SearchContext } from '../search/SearchContext'
import { canOpenFile, fileResponseError, trustedViewerHref } from './fileReadiness'
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
    const [sourceOpen, setSourceOpen] = useState<{ id: string; value: SourceOpen } | null>(null)
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
    useEffect(() => {
        if (!sourceOwned || !selected) { setSourceOpen(null); return }
        const controller = new AbortController()
        void fetch(`/api/v1/resources/${encodeURIComponent(resourceId)}/open?version_id=${encodeURIComponent(selected)}`, { signal: controller.signal })
            .then(async response => {
                if (!response.ok) throw new Error('Không thể kiểm tra khả năng mở tại kho nguồn.')
                const value: SourceOpen = await response.json()
                if (!controller.signal.aborted && value.version_id === selected)
                    setSourceOpen({ id: selected, value })
            }).catch(() => { if (!controller.signal.aborted) setSourceOpen(null) })
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
                onChange={event => { setSelected(event.target.value); setOpen(false) }}>
                {versions.map(row => <option value={row.id} key={row.id}>Phiên bản {row.version_number} · {new Date(row.created_at).toLocaleDateString('vi-VN')}</option>)}
            </select></label>
            <div className={styles.fileActions}>
            {!sourceOwned && !metadataOnly && <button className={styles.historyLink} disabled={!ready} aria-expanded={open && ready}
                onClick={() => setOpen(value => !value)}>
                {open ? <X size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}{open ? 'Đóng tệp' : 'Xem tệp'}</button>}
            {ready && <a className={styles.secondaryButton} href={`${url}?download=true`} download><Download size={15} aria-hidden="true" />Tải tệp</a>}
            {sourceOwned && sourceOpen?.id === selected && sourceOpen.value.status === 'EXTERNAL_VIEWER_AVAILABLE'
                && sourceOpen.value.files.map(file => {
                    const href = trustedViewerHref(file.viewer_url)
                    return href ? <a className={styles.secondaryButton} href={href} key={file.source_file_id}
                        target="_blank" rel="noopener noreferrer">
                        <ExternalLink size={15} aria-hidden="true" />{file.file_name || 'Mở tại kho nguồn'}
                    </a> : null
                })}
            {valid && <button className={styles.secondaryButton} onClick={() => setAttempt(n => n + 1)}><RotateCw size={14} aria-hidden="true" />Kiểm tra lại</button>}
            </div>
        </div>}
        {valid && <div role="status" className={styles.fileStatus}>
            <span className={styles.ready}><Check size={14} aria-hidden="true" />Đã tiếp nhận</span>
            {sourceOwned ? <span className={styles.pending}>Tệp thuộc kho nguồn; ETL chỉ giữ metadata và không tải nội dung</span> : <>
                <span className={status?.processing_status === 'CONTENT_READY' ? styles.ready : styles.pending}>
                    Nội dung {status?.processing_status === 'CONTENT_READY' ? 'sẵn sàng' : 'chưa sẵn sàng'}</span>
                <span className={status?.index_status === 'READY' ? styles.ready : styles.pending}>
                    Chỉ mục {status?.index_status === 'READY' ? 'sẵn sàng' : 'chưa sẵn sàng'}</span>
            </>}
            {metadataOnly && <span className={styles.pending}>Chỉ có metadata; chưa được phép đọc nội dung</span>}
        </div>}
        {statusError?.id === selected && <p role="alert" className={styles.error}>{statusError.message}</p>}
        {sourceOwned && valid && sourceOpen?.id === selected && sourceOpen.value.status !== 'EXTERNAL_VIEWER_AVAILABLE'
            && <p role="status" className={styles.message}>{({
                SOURCE_AUTH_REQUIRED: 'Cần đăng nhập tại kho nguồn để mở học liệu.',
                METADATA_ONLY: 'Nguồn chỉ cung cấp metadata hoặc chưa có viewer được xác minh.',
                SOURCE_UNAVAILABLE: 'Kho nguồn tạm thời không khả dụng.',
                SOURCE_OPEN_UNSUPPORTED: 'Kho nguồn chưa hỗ trợ mở học liệu từ hệ thống này.',
                VERSION_STALE: 'Phiên bản nguồn đã thay đổi; hãy đồng bộ lại trước khi mở.',
                ACCESS_DENIED: 'Kho nguồn từ chối quyền truy cập học liệu.',
            } as Record<SourceOpen['status'], string>)[sourceOpen.value.status]}</p>}
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
