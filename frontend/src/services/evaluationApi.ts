import type { DatasetInput, DatasetCreated, RunInput, RunCreated, EvaluationRun } from '../types/evaluation'

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

async function request(path: string, signal?: AbortSignal, body?: DatasetInput | RunInput) {
    const response = await fetch(root + path, {
        method: body ? 'POST' : 'GET', signal: AbortSignal.any([...(signal ? [signal] : []), AbortSignal.timeout(body && 'dataset_id' in body ? 240000 : 30000)]),
        headers: { Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}) },
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
export async function runEvaluation(input: RunInput, signal?: AbortSignal): Promise<RunCreated> {
    validId(input.dataset_id)
    return (await request('/runs', signal, input)).json()
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
