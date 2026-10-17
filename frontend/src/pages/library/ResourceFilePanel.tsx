import { useEffect, useState } from 'react'
import styles from './LibraryResourceDetailPage.module.css'
import { SearchContext } from '../search/SearchContext'
import { canOpenFile, fileErrorMessage } from './fileReadiness'
import type { FileIndexStatus } from './fileReadiness'

type Version = { id: string; version_number: number; created_at: string }

export function ResourceFilePanel({ resourceId, versionId, contextId, sourceOwned = false }: {
    resourceId: string; versionId: string | null; contextId: string | null; sourceOwned?: boolean
}) {
    const [versions, setVersions] = useState<Version[]>([])
    const [selected, setSelected] = useState(versionId || '')
    const [error, setError] = useState('')
    const [loading, setLoading] = useState(true)
    const [open, setOpen] = useState(false)
    const [readiness, setReadiness] = useState<{ id: string; status: FileIndexStatus } | null>(null)
    const [statusError, setStatusError] = useState<{ id: string; message: string } | null>(null)
    const [attempt, setAttempt] = useState(0)
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
    const ready = valid && canOpenFile(status) && statusError?.id !== selected
    useEffect(() => {
        if (!valid) return
        const controller = new AbortController()
        let timer: number | undefined
        async function poll() {
            try {
                const response = await fetch(`/api/v1/resource-versions/${encodeURIComponent(selected)}/index-status`, { signal: controller.signal })
                if (!response.ok) throw new Error(fileErrorMessage(response.status))
                const data: FileIndexStatus = await response.json()
                if (!controller.signal.aborted) { setReadiness({ id: selected, status: data }); setStatusError(null) }
            } catch (reason) {
                if (!controller.signal.aborted) setStatusError({ id: selected, message: reason instanceof Error ? reason.message : 'Không tải được trạng thái tệp.' })
            } finally {
                if (!controller.signal.aborted) timer = window.setTimeout(poll, document.hidden ? 15000 : 5000)
            }
        }
        void poll()
        return () => { controller.abort(); if (timer !== undefined) window.clearTimeout(timer) }
    }, [selected, valid, attempt])
    return <section className={styles.panel}>
        <h2>Tệp học liệu</h2>
        {error && <p role="alert" className={styles.error}>{error}</p>}
        {loading ? <p className={styles.message}>Đang tải danh sách tệp…</p> :
            !error && !versions.length && <p className={styles.message}>{sourceOwned
                ? 'Chưa nhập tệp từ nguồn. Kiểm tra Đồng bộ dữ liệu nguồn.'
                : 'Chưa có tệp học liệu.'}</p>}
        <div className={styles.fileToolbar}>
            <label>Phiên bản <select aria-label="Phiên bản tệp" value={selected}
                onChange={event => { setSelected(event.target.value); setOpen(false) }}>
                {versions.map(row => <option value={row.id} key={row.id}>Phiên bản {row.version_number} · {new Date(row.created_at).toLocaleDateString('vi-VN')}</option>)}
            </select></label>
            <button className={styles.historyLink} disabled={!ready} onClick={() => setOpen(value => !value)}>
                {open ? 'Đóng tệp' : 'Xem tệp'}</button>
            {ready && <a className={styles.secondaryButton} href={`${url}?download=true`} download>Tải tệp</a>}
            {valid && <button className={styles.secondaryButton} onClick={() => setAttempt(n => n + 1)}>Kiểm tra lại</button>}
        </div>
        {valid && <p role="status" className={ready ? styles.ready : styles.pending}>
            Phiên bản đã tiếp nhận · Nội dung {status?.processing_status === 'CONTENT_READY' ? 'sẵn sàng' : 'chưa sẵn sàng'} · Chỉ mục {status?.index_status === 'READY' ? 'sẵn sàng' : 'chưa sẵn sàng'}
        </p>}
        {statusError?.id === selected && <p role="alert" className={styles.error}>{statusError.message}</p>}
        {open && ready && <FilePreview key={selected} url={url} />}
        {contextId && selected === versionId && <div className={styles.fileContext}><SearchContext id={contextId} /></div>}
    </section>
}

function FilePreview({ url }: { url: string }) {
    const [file, setFile] = useState<{ url: string; type: string; size: number; text: string } | null>(null)
    const [error, setError] = useState('')
    useEffect(() => {
        const controller = new AbortController()
        let objectUrl = ''
        void fetch(url, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(60000)]) })
            .then(async response => {
                if (!response.ok) throw new Error(fileErrorMessage(response.status))
                if (Number(response.headers.get('Content-Length')) > 30 * 1024 * 1024) {
                    await response.body?.cancel()
                    throw new Error('Tệp lớn hơn 30 MB. Hãy tải về để xem.')
                }
                const blob = await response.blob()
                const text = blob.type.startsWith('text/') ? await blob.slice(0, 200000).text() : ''
                if (controller.signal.aborted) return
                objectUrl = URL.createObjectURL(blob)
                setFile({ url: objectUrl, type: blob.type.split(';')[0], size: blob.size, text })
            }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Không tải được tệp.') })
        return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
    }, [url])
    if (error) return <p role="alert" className={styles.error}>{error}</p>
    if (!file) return <p className={styles.message}>Đang tải tệp…</p>
    return <div className={styles.filePreview}>
        <p className={styles.subtitle}>{file.type === 'application/pdf' ? 'PDF' : file.type.startsWith('image/') ? 'Hình ảnh' :
            file.type.startsWith('text/') ? 'Văn bản' : 'Tệp Office'} · {(file.size / 1024 / 1024).toFixed(2)} MB</p>
        {file.type === 'application/pdf' ? <iframe title="Xem tệp PDF" src={file.url} /> :
            file.type.startsWith('image/') ? <img src={file.url} alt="Nội dung tệp học liệu" /> :
                file.type.startsWith('text/') ? <pre>{file.text}{file.size > 200000 && '\n… Tải tệp để xem toàn bộ.'}</pre> :
                    <><p className={styles.message}>Nội dung văn bản. Tải tệp để xem đầy đủ định dạng Office.</p>
                        <OfficeTextPreview url={url} /></>}
    </div>
}

function OfficeTextPreview({ url }: { url: string }) {
    const [text, setText] = useState<string | null>(null)
    const [error, setError] = useState('')
    useEffect(() => {
        const controller = new AbortController()
        void fetch(`${url}?view=text`, { signal: controller.signal }).then(async response => {
            if (!response.ok) throw new Error(fileErrorMessage(response.status))
            const value = await response.text()
            if (!controller.signal.aborted) setText(value)
        }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Không tải được nội dung.') })
        return () => controller.abort()
    }, [url])
    if (error) return <p className={styles.error} role="alert">{error}</p>
    return text === null ? <p className={styles.message}>Đang tải nội dung…</p> : <pre>{text || 'Chưa có nội dung văn bản.'}</pre>
}
