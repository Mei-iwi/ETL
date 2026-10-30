import type { ProcessingPreferencesValue } from './processingPreferences'

export type BilingualSegment = {
    segment_id: string; source_type: string; source_language: string; target_language: string
    original_text: string; translated_text: string | null; translation_status: string
    start_time?: number; end_time?: number; observed_at?: number; page_number?: number
    frame_url?: string; generated: boolean
}
export type BilingualView = {
    resource_version_id: string; items: BilingualSegment[]; original_file_url: string; original_filename?: string; source_backed?: boolean
    options: Partial<ProcessingPreferencesValue> & { translation_pending?: boolean; translation_state?: string }
    extraction: { extraction_status?: string; warnings?: string[]; media?: { audio_status?: string; visual_status?: string; selected_video_track?: number | null; frame_ocr_status?: string } }[]
    summary: { total: number; translated_count: number; timed_count: number; timed_translated_count: number; revision: string }
    pagination: { offset: number; limit: number; total: number }
}
export class BilingualApiError extends Error {
    status: number
    constructor(status: number) { super(status === 409 ? 'Nội dung đã thay đổi. Thử lại để cập nhật.' : 'Không thể tải bản đối chiếu.'); this.status = status }
}
export async function getBilingualView(versionId: string, signal: AbortSignal,
    options: { includeItems?: boolean; offset?: number; revision?: string } = {}): Promise<BilingualView> {
    const params = new URLSearchParams({ include_items: String(options.includeItems ?? false), offset: String(options.offset ?? 0), limit: '20' })
    if (options.revision) params.set('revision', options.revision)
    const response = await fetch(`/api/v1/resource-versions/${encodeURIComponent(versionId)}/bilingual?${params}`, { signal })
    if (!response.ok) throw new BilingualApiError(response.status)
    const result = await response.json() as BilingualView
    const validCount = (value: number) => Number.isInteger(value) && value >= 0
    if (result.resource_version_id !== versionId || !result.summary || !/^[a-f0-9]{64}$/.test(result.summary.revision)
        || (options.revision && result.summary.revision !== options.revision)
        || !validCount(result.summary.total) || !validCount(result.summary.translated_count)
        || !validCount(result.summary.timed_count) || !validCount(result.summary.timed_translated_count)
        || result.summary.translated_count > result.summary.total || result.summary.timed_count > result.summary.total
        || result.summary.timed_translated_count > Math.min(result.summary.timed_count, result.summary.translated_count)
        || result.pagination?.offset !== (options.offset ?? 0) || result.pagination.limit !== 20
        || result.pagination.total !== result.summary.total || !Array.isArray(result.items)
        || result.items.length > (options.includeItems ? 20 : 0)) throw new Error('Bản đối chiếu không khớp phiên bản hoặc trang yêu cầu.')
    return result
}
