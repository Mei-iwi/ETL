// Cookie authentication only. No session or password is persisted in browser storage.
const nativeFetch = window.fetch.bind(window)
let mode = 'pending'
let csrf: string | undefined
let csrfRequest: Promise<string> | undefined
let sessionRequests = new AbortController()

export function setAuthMode(value: string) { mode = value }
export function clearAuthRequests() {
    sessionRequests.abort()
    sessionRequests = new AbortController()
    csrf = undefined
    csrfRequest = undefined
}
async function csrfToken() {
    if (csrf) return csrf
    csrfRequest ??= nativeFetch('/api/v1/auth/csrf', { credentials: 'same-origin', cache: 'no-store' })
        .then(async response => {
            if (!response.ok) throw new Error('Không thể xác minh yêu cầu. Vui lòng thử lại.')
            const body = await response.json()
            if (typeof body.csrf_token !== 'string') throw new Error('Phản hồi xác thực không hợp lệ.')
            return (csrf = body.csrf_token)
        }).finally(() => { csrfRequest = undefined })
    return csrfRequest
}
window.fetch = async (input, init) => {
    const request = new Request(input instanceof Request ? input : new URL(input, location.href), init)
    const url = new URL(request.url)
    if (url.origin !== location.origin || !url.pathname.startsWith('/api/')) return nativeFetch(request)
    const headers = new Headers(request.headers)
    const authRoute = url.pathname.startsWith('/api/v1/auth/')
    if (mode !== 'legacy_local' && !['GET', 'HEAD', 'OPTIONS'].includes(request.method)) headers.set('X-CSRF-Token', await csrfToken())
    const boundary = sessionRequests
    const signal = authRoute ? request.signal : AbortSignal.any([request.signal, boundary.signal])
    const response = await nativeFetch(new Request(request, { headers, credentials: 'same-origin', cache: 'no-store', signal }))
    if (response.status === 401 && !authRoute && mode !== 'legacy_local' && boundary === sessionRequests) window.dispatchEvent(new Event('etl:session-expired'))
    if (response.status === 403 && headers.has('X-CSRF-Token')) {
        const body = await response.clone().json().catch(() => null)
        if (body?.detail?.code === 'AUTH_CSRF_INVALID') csrf = undefined
    }
    return response
}
