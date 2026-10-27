// URL/form state stays intact; result navigation must revisit live authority.
import type { SearchRequest, SearchResponse } from '../../types/search'

// Compatibility API: no cached result/snippet without a server visibility epoch.
export function clearSearchViewCache() { sessions.clear(); epoch++ }
export function readSearchView(_key: string, _now = Date.now()): SearchResponse | null { return null }
export function saveSearchView(_key: string, _value: SearchResponse, _now = Date.now()) {}

// Tokens only: protected snippets/windows/URLs are never replayed locally. Each
// navigation still asks the server to authorize and hydrate the requested page.
const sessions = new Map<string, { id: string; until: number }>()
let epoch = 0
type Transport = {
    full: (request: SearchRequest, signal?: AbortSignal) => Promise<SearchResponse>
    page: (request: SearchRequest & { session_id: string }, signal?: AbortSignal) => Promise<SearchResponse>
}
export async function fetchSearchPage(key: string, criteria: SearchRequest, signal: AbortSignal,
    transport: Transport, now = Date.now): Promise<SearchResponse> {
    const started = epoch
    const identity = JSON.stringify([key, { ...criteria, page: 1, window_pages: 1 }])
    const previous = sessions.get(identity)
    let result: SearchResponse | undefined
    const check = () => {
        signal.throwIfAborted()
        if (started !== epoch) throw new DOMException('Search scope changed', 'AbortError')
    }
    check()
    try {
        if (previous && previous.until > now()) {
            sessions.delete(identity); sessions.set(identity, previous)
            try {
                result = await transport.page({ ...criteria, session_id: previous.id, window_pages: 1 }, signal)
            } catch (error) {
                check()
                sessions.delete(identity)
                // Only a typed session miss may cause ONE full-search fallback.
                if (!(error instanceof Error && 'status' in error && error.status === 409
                    && 'code' in error && typeof error.code === 'string' && error.code.startsWith('SEARCH_SESSION_'))) throw error
            }
        }
        check()
        // Old servers reject unknown request fields. Window prefetch is not
        // requested until a trustworthy browser revocation policy exists.
        const legacyCriteria = { ...criteria }
        delete legacyCriteria.window_pages
        result ??= await transport.full(legacyCriteria, signal)
        check()
        const info = result.search_session
        if (info && typeof info.id === 'string' && /^[A-Za-z0-9_-]{40,64}$/.test(info.id)
            && Number.isFinite(info.ttl_seconds) && info.ttl_seconds > 0 && info.ttl_seconds <= 60
            && result.total_exact === true && !result.session_status) {
            sessions.set(identity, { id: info.id, until: now() + info.ttl_seconds * 1000 })
            while (sessions.size > 8) sessions.delete(sessions.keys().next().value!)
        } else sessions.delete(identity)
        return result
    } catch (error) {
        if (started === epoch) sessions.delete(identity)
        throw error
    }
}
