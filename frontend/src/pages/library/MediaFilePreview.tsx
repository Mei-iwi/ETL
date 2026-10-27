import { useEffect, useState } from 'react'
import { getMediaFileType } from '../../services/processingPreferences'
import styles from './LibraryResourceDetailPage.module.css'

export function MediaPlayer({ url, kind }: { url: string; kind: 'audio' | 'video' }) {
    const [failed, setFailed] = useState(false)
    return <div className={styles.mediaPreview}>
        {kind === 'audio'
            ? <audio controls preload="metadata" src={url} aria-label="Nghe học liệu MP3" onError={() => setFailed(true)} />
            : <video controls playsInline preload="metadata" src={url} aria-label="Xem học liệu MP4" onError={() => setFailed(true)} />}
        {failed && <p role="alert">Trình duyệt không phát được tệp này. Hãy kiểm tra định dạng hoặc tải tệp để mở bằng trình phát khác.</p>}
    </div>
}

export function SelectedMediaPreview({ file }: { file: File | null }) {
    const kind = getMediaFileType(file?.name)
    const [preview, setPreview] = useState<{ file: File; url: string } | null>(null)
    useEffect(() => {
        if (!file || !kind) return
        const url = URL.createObjectURL(file)
        setPreview({ file, url })
        return () => URL.revokeObjectURL(url)
    }, [file, kind])
    if (!kind || !preview || preview.file !== file) return null
    return <section aria-label="Xem thử tệp đã chọn">
        <p>{kind === 'audio' ? 'Nghe thử MP3' : 'Xem thử MP4'}</p>
        <MediaPlayer key={preview.url} url={preview.url} kind={kind} />
    </section>
}
