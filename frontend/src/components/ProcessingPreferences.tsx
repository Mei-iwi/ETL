import type { ProcessingPreferencesValue } from '../services/processingPreferences'
import styles from './MediaProcessing.module.css'

export function ProcessingPreferences({ value, onChange, disabled = false, mediaType, variant = 'embedded' }: {
    value: ProcessingPreferencesValue
    onChange: (value: ProcessingPreferencesValue) => void
    disabled?: boolean
    mediaType?: 'audio' | 'video'
    variant?: 'card' | 'embedded'
}) {
    return <section className={variant === 'card' ? styles.card : styles.preferences}>
        <h2 className={styles.heading}>{mediaType ? 'Tùy chọn nhận diện và bản dịch' : 'Ngôn ngữ và bản dịch'}</h2>
        <p className={styles.description}>Chọn ngôn ngữ trước khi bắt đầu xử lý. {mediaType === 'video' && 'Video có âm thanh sẽ nhận dạng giọng nói, không lấy frame/OCR. OCR chỉ dùng cho video không có track âm thanh.'}</p>
        <fieldset disabled={disabled} className={styles.fields} aria-label="Tùy chọn ngôn ngữ và nhận diện">
            <div className={styles.grid}>
                <label className={styles.field}>Ngôn ngữ nguồn
                    <input aria-label="Ngôn ngữ nguồn" value={value.source_language ?? ''} maxLength={3}
                        pattern="[a-z]{2,3}" placeholder="Tự động nhận diện"
                        onChange={e => onChange({ ...value, source_language: e.target.value || null })} />
                    <span className={styles.hint}>Nhập vi, en, fr, ja… nếu biết ngôn ngữ để hỗ trợ nhận dạng chính xác hơn; để trống để tự nhận diện.</span>
                </label>
                <label className={styles.field}>Ngôn ngữ bản dịch
                    <input aria-label="Ngôn ngữ bản dịch" value={value.target_language} required pattern="[a-z]{2,3}" maxLength={3}
                        placeholder="vi"
                        onChange={e => onChange({ ...value, target_language: e.target.value })} />
                    <span className={styles.hint}>Mặc định vi — tiếng Việt.</span>
                </label>
            </div>
            {mediaType === 'video' && <label className={styles.checkbox}>
                <input type="checkbox" checked={value.ocr_enabled}
                    onChange={e => onChange({ ...value, ocr_enabled: e.target.checked })} />
                Đọc chữ trong video không có âm thanh (OCR)
            </label>}
            {mediaType && <details className={styles.advanced}>
                <summary>Tùy chọn nâng cao</summary>
                <div className={styles.grid}>
                    <label className={styles.field}>Track âm thanh
                        <input aria-label="Track âm thanh" type="number" min={0} max={63} value={value.audio_track ?? ''}
                            placeholder="Tự động chọn"
                            onChange={e => onChange({ ...value, audio_track: e.target.value === '' ? null : Number(e.target.value) })} />
                        <span className={styles.hint}>Để trống để tự chọn. Chỉ số stream trong tệp, không phải số thứ tự âm thanh; video thường dùng stream 0 cho hình và 1 cho âm thanh.</span>
                    </label>
                    {mediaType === 'video' && <>
                        <label className={styles.field}>Track phụ đề
                            <input aria-label="Track phụ đề" type="number" min={0} max={63} value={value.subtitle_track ?? ''}
                                placeholder="Tự động chọn"
                                onChange={e => onChange({ ...value, subtitle_track: e.target.value === '' ? null : Number(e.target.value) })} />
                            <span className={styles.hint}>Để trống nếu không biết chỉ số stream phụ đề hoặc tệp không có phụ đề.</span>
                        </label>
                        <label className={styles.field}>Ngôn ngữ OCR
                            <input aria-label="Gói ngôn ngữ OCR" value={value.ocr_languages ?? ''} placeholder="vie+eng"
                                pattern="[a-z]{3}(\+[a-z]{3})*" disabled={!value.ocr_enabled}
                                onChange={e => onChange({ ...value, ocr_languages: e.target.value || null })} />
                            <span className={styles.hint}>Để trống để dùng ngôn ngữ đã cấu hình.</span>
                        </label>
                    </>}
                </div>
            </details>}
        </fieldset>
    </section>
}
