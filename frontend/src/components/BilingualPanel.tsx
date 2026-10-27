import { useEffect, useRef, useState } from 'react'
import { ProcessingPreferences } from './ProcessingPreferences'
import { defaultProcessingPreferences, getMediaFileType, saveProcessingPreferences } from '../services/processingPreferences'
import type { ProcessingPreferencesValue } from '../services/processingPreferences'
import styles from './MediaProcessing.module.css'

type Segment = {
    segment_id: string; source_type: string; source_language: string; target_language: string
    original_text: string; translated_text: string | null; translation_status: string
    start_time?: number; end_time?: number; observed_at?: number; page_number?: number
    frame_url?: string; generated: boolean
}
type View = {
    items: Segment[]; original_file_url: string; original_filename?: string
    options: Partial<ProcessingPreferencesValue> & { translation_pending?: boolean; translation_state?: string }
    extraction: { extraction_status?: string; warnings?: string[]; media?: { audio_status?: string; visual_status?: string; selected_video_track?: number | null; frame_ocr_status?: string } }[]
}

export function BilingualPanel({ versionId, refreshKey }: { versionId: string; refreshKey: string }) {
    const [view, setView] = useState<View | null>(null)
    const [error, setError] = useState('')
    const [preferences, setPreferences] = useState(defaultProcessingPreferences)
    const [mode, setMode] = useState('parallel')
    const [busy, setBusy] = useState(false)
    const [reload, setReload] = useState(0)
    const player = useRef<HTMLVideoElement>(null)
    const pendingSeek = useRef<number | null>(null)
    const hydrated = useRef(false)
    const [playerVisible, setPlayerVisible] = useState(false)
    useEffect(() => {
        const controller = new AbortController()
        let timer: ReturnType<typeof setTimeout>
        async function load() {
            try {
                const response = await fetch(`/api/v1/resource-versions/${encodeURIComponent(versionId)}/bilingual`, { signal: controller.signal })
                if (!response.ok) throw new Error('Không thể tải bản đối chiếu.')
                const result = await response.json() as View
                setView(result)
                if (!hydrated.current) {
                    setPreferences({ ...defaultProcessingPreferences, ...result.options })
                    hydrated.current = true
                }
                setError('')
                if (result.options.translation_pending) timer = setTimeout(() => { void load() }, 4000)
            } catch (caught) {
                if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : 'Không thể tải nội dung.')
            }
        }
        if (versionId) void load()
        return () => { controller.abort(); clearTimeout(timer) }
    }, [versionId, refreshKey, reload])

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

    const hasTranslation = view?.items.some(s => Boolean(s.translated_text))
    const hasTimed = view?.items.some(s => ['speech', 'subtitle'].includes(s.source_type) && s.start_time != null && s.end_time != null)
    const hasTimedTranslation = view?.items.some(s => ['speech', 'subtitle'].includes(s.source_type) && s.start_time != null && s.end_time != null && s.translated_text)
    const mediaType = getMediaFileType(view?.original_filename) ?? (view?.extraction.some(e => e.media)
        ? view.extraction.some(e => e.media?.selected_video_track != null) ? 'video' : 'audio' : undefined)
    return <section className={`${styles.card} ${styles.panel}`}>
        <h2 className={styles.heading}>Nguyên ngữ và bản dịch</h2>
        <ProcessingPreferences value={preferences} onChange={setPreferences} disabled={busy} mediaType={mediaType} />
        <div className={styles.actions}>
            <button className={styles.primaryButton} type="button" disabled={busy || Boolean(view?.items.length)} onClick={() => { void configure(false) }}>Lưu tùy chọn trước xử lý</button>
            <button className={styles.secondaryButton} type="button" disabled={busy || !view?.items.length || view.options.translation_pending} onClick={() => { void configure(true) }}>Dịch lại / sửa ngôn ngữ nguồn</button>
        </div>
        {error && <p className={styles.alert} role="alert">{error}</p>}
        {view?.options.translation_pending && <p role="status">Đang chờ worker trích xuất hoặc dịch.</p>}
        {view?.options.translation_state === 'partial' && <p>Một số đoạn chưa dịch được. Nguyên ngữ vẫn được giữ; có thể kiểm tra model/ngôn ngữ rồi thử dịch lại.</p>}
        {view?.extraction.map((entry, index) => <p key={index}>
            Trích xuất: {entry.extraction_status ?? 'completed'} · Âm thanh: {entry.media?.audio_status ?? 'không áp dụng'}
            {entry.media?.frame_ocr_status === 'skipped_audio_present' && ' · Bỏ lấy frame/OCR vì video có track âm thanh'}
            {entry.warnings?.length ? ` · Cảnh báo: ${entry.warnings.join(', ')}` : ''}
        </p>)}
        {view && <p><a href={view.original_file_url} target="_blank" rel="noreferrer">Mở tệp gốc</a>{' · '}
            <a href={`/api/v1/resource-versions/${versionId}/exports/json`}>JSON kèm nguồn</a>{' · '}
            {view.items.length > 0 && <a href={`/api/v1/resource-versions/${versionId}/exports/txt`}>TXT nguyên ngữ</a>}{' · '}
            {hasTranslation && <a href={`/api/v1/resource-versions/${versionId}/exports/txt?translated=true`}>TXT bản dịch</a>}{' · '}
            {hasTimed && <><a href={`/api/v1/resource-versions/${versionId}/exports/srt`}>SRT</a>{' · '}
                <a href={`/api/v1/resource-versions/${versionId}/exports/vtt`}>VTT</a></>}
            {hasTimedTranslation && <>{' · '}<a href={`/api/v1/resource-versions/${versionId}/exports/srt?translated=true`}>SRT bản dịch</a>{' · '}
                <a href={`/api/v1/resource-versions/${versionId}/exports/vtt?translated=true`}>VTT bản dịch</a></>}
        </p>}
        {playerVisible && view && <video ref={player} src={view.original_file_url} controls preload="metadata"
            onLoadedMetadata={() => { if (player.current && pendingSeek.current != null) player.current.currentTime = pendingSeek.current }}
            className={styles.player} />}
        <label className={styles.field}>Hiển thị <select value={mode} onChange={e => setMode(e.target.value)}>
            <option value="parallel">Đối chiếu hai cột</option><option value="original">Nguyên ngữ</option><option value="translated">Bản dịch</option>
        </select></label>
        {view?.items.map(segment => <article key={segment.segment_id} className={styles.segment}>
            <div className={styles.segmentMeta}>{segment.source_type} · {segment.source_language} → {segment.target_language} · {segment.translation_status}
                {segment.generated && <strong> · Mô tả do mô hình sinh</strong>}
                {segment.page_number != null && <a href={`${view.original_file_url}#page=${segment.page_number}`} target="_blank" rel="noreferrer"> · Trang {segment.page_number}</a>}
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
    </section>
}
