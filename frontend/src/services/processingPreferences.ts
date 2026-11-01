export type ProcessingPreferencesValue = {
    source_language: string | null
    target_language: string
    ocr_enabled: boolean
    ocr_languages: string | null
    visual_enabled: boolean
    audio_track: number | null
    subtitle_track: number | null
}

export function getMediaFileType(filename?: string): 'audio' | 'video' | undefined {
    if (/\.mp3$/i.test(filename ?? '')) return 'audio'
    if (/\.mp4$/i.test(filename ?? '')) return 'video'
    return undefined
}

export const defaultProcessingPreferences: ProcessingPreferencesValue = {
    source_language: null, target_language: 'vi', ocr_enabled: true,
    ocr_languages: null, visual_enabled: false, audio_track: null, subtitle_track: null,
}

export async function saveProcessingPreferences(versionId: string, value: ProcessingPreferencesValue) {
    const response = await fetch(`/api/v1/resource-versions/${encodeURIComponent(versionId)}/processing-options`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
    })
    if (!response.ok) {
        const body = await response.json().catch(() => null)
        const detail = body?.detail
        const code = typeof detail === 'string' ? detail : detail?.code
        if (code === 'PROCESSING_BUSY' || code === 'EXTRACTION_OPTIONS_LOCKED_USE_NEW_VERSION') {
            throw new Error('Học liệu đã bắt đầu xử lý với tùy chọn khác. Hãy kiểm tra trạng thái xử lý trước khi thay đổi.')
        }
        throw new Error(detail?.message ?? `Không thể lưu tùy chọn xử lý${code ? ` (${code})` : ''}.`)
    }
}
