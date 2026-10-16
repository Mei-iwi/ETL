import type { DatasetInput, DatasetCreated, RunInput, RunCreated, EvaluationRun, EvaluationProgress } from '../types/evaluation'

export class EvaluationApiError extends Error {
    status: number
    code: string
    constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code }
}

const root = '/api/v1/evaluations'
const validId = (id: string) => {
    if (!/^[0-7][0-9A-HJKMNP-TV-Z]{25}$/.test(id)) throw new Error('Mã dataset/run không hợp lệ.')
    return id
}

async function request(path: string, signal?: AbortSignal, body?: DatasetInput | RunInput, accept = 'application/json') {
    const response = await fetch(root + path, {
        method: body ? 'POST' : 'GET', signal: AbortSignal.any([...(signal ? [signal] : []), AbortSignal.timeout(body && 'dataset_id' in body ? 660000 : 30000)]),
        headers: { Accept: accept, ...(body ? { 'Content-Type': 'application/json' } : {}) },
        ...(body ? { body: JSON.stringify(body) } : {}),
    })
    if (!response.ok) {
        const payload = await response.json().catch(() => null)
        const detail = payload?.detail
        const message = typeof detail?.message === 'string' ? detail.message : response.status === 422
            ? 'Dữ liệu chưa hợp lệ. Kiểm tra nhãn, ID và tham số thực nghiệm.'
            : `Không thực hiện được yêu cầu thực nghiệm (HTTP ${response.status}).`
        throw new EvaluationApiError(response.status, typeof detail?.code === 'string' ? detail.code : 'EVALUATION_UNAVAILABLE', message)
    }
    return response
}

export async function createDataset(input: DatasetInput, signal?: AbortSignal): Promise<DatasetCreated> {
    return (await request('/datasets', signal, input)).json()
}
export async function runEvaluation(input: RunInput, signal?: AbortSignal, onProgress?: (progress: EvaluationProgress) => void): Promise<RunCreated> {
    validId(input.dataset_id)
    const response = await request('/runs', signal, input, onProgress ? 'application/x-ndjson' : 'application/json')
    if (!onProgress) return response.json()
    if (!response.headers.get('content-type')?.includes('application/x-ndjson') || !response.body) throw new Error('Backend chưa hỗ trợ tiến độ. Hãy khởi động lại Backend mới.')
    const reader = response.body.getReader(), decoder = new TextDecoder()
    let buffer = ''
    try {
        while (true) {
            const { value, done } = await reader.read()
            buffer += decoder.decode(value, { stream: !done })
            if (buffer.length > 65536) throw new Error('Dữ liệu tiến độ không hợp lệ.')
            const lines = buffer.split('\n'); buffer = lines.pop() || ''
            for (const line of lines) {
                if (!line.trim()) continue
                const event = JSON.parse(line)
                if (!event || typeof event !== 'object') throw new Error('Dữ liệu tiến độ không hợp lệ.')
                if (event.type === 'error') throw new EvaluationApiError(event.status || 503, event.detail?.code || 'EVALUATION_UNAVAILABLE', event.detail?.message || 'Không hoàn tất thực nghiệm.')
                if (!Number.isInteger(event.completed) || !Number.isInteger(event.total) || event.total < 1 || event.completed < 0 || event.completed > event.total || !Number.isInteger(event.percent) || event.percent < 0 || event.percent > 100) throw new Error('Dữ liệu tiến độ không hợp lệ.')
                if (event.type === 'result') {
                    validId(event.result?.run_id)
                    if (event.percent === 100 && (event.result.status !== 'COMPLETED' || event.completed !== event.total)) throw new Error('Trạng thái hoàn tất không hợp lệ.')
                    onProgress({ ...event, phase: 'finished' })
                    return event.result as RunCreated
                }
                if (event.type !== 'progress' || event.percent >= 100) throw new Error('Dữ liệu tiến độ không hợp lệ.')
                onProgress(event)
            }
            if (done) throw new Error('Mất kết nối trước khi hoàn tất. Kiểm tra Run ID trước khi chạy lại.')
        }
    } finally { await reader.cancel().catch(() => {}); reader.releaseLock() }
}
export async function getEvaluationRun(id: string, offset = 0, signal?: AbortSignal): Promise<EvaluationRun> {
    return (await request(`/runs/${validId(id)}?offset=${offset}&limit=20`, signal)).json()
}
export async function getEvaluationReport(id: string, format: 'csv' | 'json', signal?: AbortSignal) {
    const response = await request(`/runs/${validId(id)}/report?format=${format}`, signal)
    return { blob: await response.blob(), filename: `evaluation-${id}.${format}` }
}

// IDs only; never persist queries, qrels, run bodies or credentials in the browser.
export function recentEvaluationId(kind: 'dataset' | 'run', value?: string) {
    try {
        if (value) localStorage.setItem(`evaluation-${kind}-id`, validId(value))
        return localStorage.getItem(`evaluation-${kind}-id`) || ''
    } catch { return '' }
}
