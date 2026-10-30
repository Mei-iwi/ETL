import { useEffect, useState } from 'react'
import { FileText, Eye, X, Download, ExternalLink, RotateCw, Check } from 'lucide-react'
import styles from './LibraryResourceDetailPage.module.css'
import { SearchContext } from '../search/SearchContext'
import { canOpenFile, fileResponseError } from './fileReadiness'
import type { FileIndexStatus } from './fileReadiness'
import { MEDIA_PREVIEW_MAX_BYTES, readPreviewBlob } from './previewBody'
import { originalSourceUrl } from './sourceReference'
import type { SourceInfo } from './sourceReference'
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
    const [sourceInfo, setSourceInfo] = useState<{ id: string; value: SourceInfo } | null>(null)
    const [sourceError, setSourceError] = useState<{ id: string; message: string } | null>(null)
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
    const ready = valid && !metadataOnly && canOpenFile(status) && statusError?.id !== selected
    const original = sourceInfo?.id === selected ? originalSourceUrl(sourceInfo.value) : null
    const externalLinkReason = !ready ? 'Tệp chưa sẵn sàng để truy cập.'
        : sourceError?.id === selected ? sourceError.message
            : sourceInfo?.id !== selected ? 'Đang kiểm tra liên kết nguồn…'
                : sourceInfo.value.provider !== 'google_drive' ? 'Nguồn hiện tại không cung cấp liên kết mở bên ngoài.'
                    : 'Nguồn chưa cung cấp liên kết mở trực tiếp cho tệp này.'
    useEffect(() => {
        if (!ready || !sourceOwned) return
        const controller = new AbortController()
        void fetch(`/api/v1/resource-versions/${encodeURIComponent(selected)}/source-info`, { signal: controller.signal })
            .then(async response => {
                if (!response.ok) throw new Error(await fileResponseError(response))
                const value: SourceInfo = await response.json()
                if (!controller.signal.aborted) { setSourceInfo({ id: selected, value }); setSourceError(null) }
            }).catch(reason => {
                if (!controller.signal.aborted) {
                    setSourceInfo(null)
                    setSourceError({ id: selected, message: reason instanceof Error ? reason.message : 'Nguồn hiện không khả dụng.' })
                }
            })
        return () => controller.abort()
    }, [selected, ready, sourceOwned, attempt])
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
            {!metadataOnly && <button className={styles.historyLink} disabled={!ready} aria-expanded={open && ready}
                onClick={() => setOpen(value => !value)}>
                {open ? <X size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}{open ? 'Đóng tệp' : 'Xem tệp'}</button>}
            {ready && <a className={styles.secondaryButton} href={`${url}?download=true`} download><Download size={15} aria-hidden="true" />Tải tệp</a>}
            {ready && original && <a className={styles.secondaryButton} href={original}
                target="_blank" rel="noopener noreferrer" title="Xem tệp trực tiếp tại nguồn trong tab mới"><ExternalLink size={15} aria-hidden="true" />Xem tệp bên ngoài</a>}
            {sourceOwned && valid && !metadataOnly && !(ready && original) && <button className={styles.secondaryButton}
                disabled title={externalLinkReason} aria-describedby="external-source-link-status"><ExternalLink size={15} aria-hidden="true" />Xem tệp bên ngoài</button>}
            {valid && <button className={styles.secondaryButton} onClick={() => setAttempt(n => n + 1)}><RotateCw size={14} aria-hidden="true" />Kiểm tra lại</button>}
            </div>
        </div>}
        {valid && <div role="status" className={styles.fileStatus}>
            <span className={styles.ready}><Check size={14} aria-hidden="true" />Đã tiếp nhận</span>
            <span className={status?.processing_status === 'CONTENT_READY' ? styles.ready : styles.pending}>
                Nội dung {status?.processing_status === 'CONTENT_READY' ? 'sẵn sàng' : 'chưa sẵn sàng'}</span>
            <span className={status?.index_status === 'READY' ? styles.ready : styles.pending}>
                Chỉ mục {status?.index_status === 'READY' ? 'sẵn sàng' : 'chưa sẵn sàng'}</span>
            {metadataOnly && <span className={styles.pending}>Chỉ có metadata; chưa được phép đọc nội dung</span>}
        </div>}
        {statusError?.id === selected && <p role="alert" className={styles.error}>{statusError.message}</p>}
        {sourceOwned && valid && !metadataOnly && !(ready && original) && <p id="external-source-link-status" className={styles.message}>{externalLinkReason}</p>}
        {sourceError?.id === selected && <p role="alert" className={styles.error}>{sourceError.message}</p>}
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
