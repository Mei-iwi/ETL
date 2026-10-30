import { useEffect, useRef, useState } from 'react'
import { ProcessingPreferences } from './ProcessingPreferences'
import { defaultProcessingPreferences, getMediaFileType, saveProcessingPreferences } from '../services/processingPreferences'
import styles from './MediaProcessing.module.css'
import { startVisiblePolling } from '../pages/system/visiblePolling'
import { BilingualApiError, getBilingualView } from '../services/bilingualApi'
import type { BilingualView } from '../services/bilingualApi'

export function BilingualPanel({ versionId, refreshKey }: { versionId: string; refreshKey: string }) {
    const [view, setView] = useState<BilingualView | null>(null)
    const [error, setError] = useState('')
    const [preferences, setPreferences] = useState(defaultProcessingPreferences)
    const [mode, setMode] = useState('parallel')
    const [busy, setBusy] = useState(false)
    const [reload, setReload] = useState(0)
    const player = useRef<HTMLVideoElement>(null)
    const pendingSeek = useRef<number | null>(null)
    const hydrated = useRef(false)
    const [playerVisible, setPlayerVisible] = useState(false)
    const [expanded, setExpanded] = useState(false)
    const [page, setPage] = useState(0)
    const pageSize = 20
    const total = view?.summary.total ?? 0
    const revision = view?.summary.revision
    const currentPage = Math.min(page, Math.max(0, Math.ceil(total / pageSize) - 1))
    const pageKey = JSON.stringify([revision, currentPage])
    const [pageData, setPageData] = useState<{ key: string; view: BilingualView } | null>(null)
    const [pageError, setPageError] = useState<{ key: string; message: string; changed: boolean } | null>(null)
    const [pageAttempt, setPageAttempt] = useState(0)
    useEffect(() => {
        let pending = false
        const polling = startVisiblePolling(async signal => {
            await Promise.resolve()
            if (signal.aborted) return
            try {
                const result = await getBilingualView(versionId, signal)
                if (signal.aborted) return
                setView(result)
                if (!hydrated.current) {
                    setPreferences({ ...defaultProcessingPreferences, ...result.options })
                    hydrated.current = true
                }
                setError('')
                pending = Boolean(result.options.translation_pending)
            } catch (caught) {
                if (!signal.aborted) {
                    pending = false
                    setView(null); setPageData(null)
                    setError(caught instanceof Error ? caught.message : 'Không thể tải nội dung.')
                }
            }
        }, () => pending, document, { busy: 4000, idle: null })
        return () => polling.stop()
    }, [versionId, refreshKey, reload])

    useEffect(() => {
        if (!expanded || !revision) return
        const controller = new AbortController()
        void Promise.resolve().then(() => controller.signal.aborted ? null : getBilingualView(versionId,
            controller.signal, { includeItems: true, offset: currentPage * pageSize, revision }))
            .then(result => { if (result && !controller.signal.aborted) { setPageData({ key: pageKey, view: result }); setPageError(null) } })
            .catch(reason => { if (!controller.signal.aborted) setPageError({ key: pageKey,
                message: reason instanceof Error ? reason.message : 'Không tải được nội dung.',
                changed: reason instanceof BilingualApiError && reason.status === 409 }) })
        return () => controller.abort()
    }, [expanded, revision, currentPage, versionId, pageKey, pageAttempt])

    async function configure(retry: boolean) {
        setBusy(true)
        try {
            if (retry) {
                const response = await fetch(`/api/v1/resource-versions/${encodeURIComponent(versionId)}/translations/retry`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ source_language: preferences.source_language, target_language: preferences.target_language }),
                })
                if (!response.ok) throw new Error('Không thể xếp hàng dịch lại. Kiểm tra job đang chạy hoặc giới hạn retry.')
            } else await saveProcessingPreferences(versionId, preferences)
            setReload(current => current + 1)
            setError('')
        } catch (caught) { setError(caught instanceof Error ? caught.message : 'Không thể lưu tùy chọn.') }
        finally { setBusy(false) }
    }

    const hasTranslation = (view?.summary.translated_count ?? 0) > 0
    const hasTimed = (view?.summary.timed_count ?? 0) > 0
    const hasTimedTranslation = (view?.summary.timed_translated_count ?? 0) > 0
    const pageItems = pageData?.key === pageKey ? pageData.view.items : null
    const pageMessage = pageError?.key === pageKey ? pageError : null
    const mediaType = getMediaFileType(view?.original_filename) ?? (view?.extraction.some(e => e.media)
        ? view.extraction.some(e => e.media?.selected_video_track != null) ? 'video' : 'audio' : undefined)
    return <section className={`${styles.card} ${styles.panel}`}>
        <h2 className={styles.heading}>Nguyên ngữ và bản dịch</h2>
        <ProcessingPreferences value={preferences} onChange={setPreferences} disabled={busy} mediaType={mediaType} />
        <div className={styles.actions}>
            <button className={styles.primaryButton} type="button" disabled={busy || !view || total > 0} onClick={() => { void configure(false) }}>Lưu tùy chọn trước xử lý</button>
            <button className={styles.secondaryButton} type="button" disabled={busy || !view || total === 0 || view.options.translation_pending} onClick={() => { void configure(true) }}>Dịch lại / sửa ngôn ngữ nguồn</button>
        </div>
        {error && <p className={styles.alert} role="alert">{error} <button type="button" onClick={() => setReload(value => value + 1)}>Thử lại</button></p>}
        {view?.options.translation_pending && <p role="status">Đang chờ worker trích xuất hoặc dịch.</p>}
        {view?.options.translation_state === 'partial' && <p>Một số đoạn chưa dịch được. Nguyên ngữ vẫn được giữ; có thể kiểm tra model/ngôn ngữ rồi thử dịch lại.</p>}
        {view?.extraction.map((entry, index) => <p key={index}>
            Trích xuất: {entry.extraction_status ?? 'completed'} · Âm thanh: {entry.media?.audio_status ?? 'không áp dụng'}
            {entry.media?.frame_ocr_status === 'skipped_audio_present' && ' · Bỏ lấy frame/OCR vì video có track âm thanh'}
            {entry.warnings?.length ? ` · Cảnh báo: ${entry.warnings.join(', ')}` : ''}
        </p>)}
        {view && <p><a href={view.original_file_url} target="_blank" rel="noreferrer">Mở tệp gốc</a>{' · '}
            {view.source_backed && <><button type="button" aria-controls="processing-source-preview" aria-expanded={playerVisible}
                onClick={() => setPlayerVisible(current => !current)}>{playerVisible ? 'Đóng tệp' : 'Xem tệp'}</button>{' · '}</>}
            <a href={`/api/v1/resource-versions/${versionId}/exports/json`}>JSON kèm nguồn</a>{' · '}
            {total > 0 && <a href={`/api/v1/resource-versions/${versionId}/exports/txt`}>TXT nguyên ngữ</a>}{' · '}
            {hasTranslation && <a href={`/api/v1/resource-versions/${versionId}/exports/txt?translated=true`}>TXT bản dịch</a>}{' · '}
            {hasTimed && <><a href={`/api/v1/resource-versions/${versionId}/exports/srt`}>SRT</a>{' · '}
                <a href={`/api/v1/resource-versions/${versionId}/exports/vtt`}>VTT</a></>}
            {hasTimedTranslation && <>{' · '}<a href={`/api/v1/resource-versions/${versionId}/exports/srt?translated=true`}>SRT bản dịch</a>{' · '}
                <a href={`/api/v1/resource-versions/${versionId}/exports/vtt?translated=true`}>VTT bản dịch</a></>}
        </p>}
        {playerVisible && view?.source_backed && <iframe id="processing-source-preview" title="Xem tệp từ nguồn" src={`${view.original_file_url}?embedded=true`}
            className={styles.player} style={{ minHeight: 560, width: '100%' }}
            referrerPolicy="strict-origin-when-cross-origin" allowFullScreen />}
        {playerVisible && view && !view.source_backed && <video ref={player} src={view.original_file_url} controls preload="metadata"
            onLoadedMetadata={() => { if (player.current && pendingSeek.current != null) player.current.currentTime = pendingSeek.current }}
            className={styles.player} />}
        <details onToggle={event => { setExpanded(event.currentTarget.open); if (!event.currentTarget.open) { setPageData(null); setPageError(null) } }}>
        <summary aria-expanded={expanded} aria-controls="bilingual-segments">Xem nội dung đối chiếu ({total})</summary>
        {expanded && <div id="bilingual-segments" aria-busy={!pageItems && !pageMessage}>
        {pageMessage ? <p role="alert">{pageMessage.message} <button type="button" onClick={() => {
            setPageData(null); setPageError(null)
            if (pageMessage.changed) { setView(null); setReload(value => value + 1) }
            else setPageAttempt(value => value + 1)
        }}>Thử lại</button></p> : !pageItems && <p role="status">Đang tải nội dung…</p>}
        <label className={styles.field}>Hiển thị <select value={mode} onChange={e => setMode(e.target.value)}>
            <option value="parallel">Đối chiếu hai cột</option><option value="original">Nguyên ngữ</option><option value="translated">Bản dịch</option>
        </select></label>
        {!pageMessage && pageItems?.map(segment => <article key={segment.segment_id} className={styles.segment}>
            <div className={styles.segmentMeta}>{segment.source_type} · {segment.source_language} → {segment.target_language} · {segment.translation_status}
                {segment.generated && <strong> · Mô tả do mô hình sinh</strong>}
                {segment.page_number != null && <a href={`${view?.original_file_url}#page=${segment.page_number}`} target="_blank" rel="noreferrer"> · Trang {segment.page_number}</a>}
                {(segment.start_time ?? segment.observed_at) != null && <button className={styles.timestamp} type="button" onClick={() => {
                    const time = segment.start_time ?? segment.observed_at ?? 0
                    pendingSeek.current = time
                    setPlayerVisible(true)
                    if (player.current && player.current.readyState >= 1) player.current.currentTime = time
                }}>{(segment.start_time ?? segment.observed_at ?? 0).toFixed(2)} giây{segment.observed_at != null ? ' (quan sát OCR)' : ''}</button>}
                {segment.frame_url && <a href={segment.frame_url} target="_blank" rel="noreferrer"> · Khung hình</a>}
            </div>
            <div className={`${styles.texts} ${mode === 'parallel' ? styles.parallel : ''}`}>
                {mode !== 'translated' && <p lang={segment.source_language}>{segment.original_text}</p>}
                {mode !== 'original' && <p lang={segment.target_language}>{segment.translated_text ?? 'Chưa có bản dịch.'}</p>}
            </div>
        </article>)}
        {view && total > pageSize && <div className={styles.actions}>
            <button type="button" disabled={currentPage === 0} onClick={() => setPage(Math.max(0, currentPage - 1))}>Trước</button>
            <span>Trang {currentPage + 1} / {Math.ceil(total / pageSize)}</span>
            <button type="button" disabled={(currentPage + 1) * pageSize >= total} onClick={() => setPage(currentPage + 1)}>Tiếp</button>
        </div>}
        </div>}
        </details>
    </section>
}
