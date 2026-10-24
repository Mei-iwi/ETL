// Tab-memory snapshots only: never localStorage, sessionStorage or file access grants.
import type { SearchResponse } from '../../types/search'

const entries = new Map<string, { value: SearchResponse; expires: number }>()
const ttl = 60_000
export function clearSearchViewCache() { entries.clear() }
export function readSearchView(key: string, now = Date.now()): SearchResponse | null {
    const entry = entries.get(key)
    if (!entry) return null
    if (entry.expires <= now) { entries.delete(key); return null }
    return entry.value
}
export function saveSearchView(key: string, value: SearchResponse, now = Date.now()) {
    // Bound retained text, including meaning variants, and page count.
    if (JSON.stringify(value).length > 250_000) return
    entries.delete(key)
    entries.set(key, { value, expires: now + ttl })
    while (entries.size > 8) entries.delete(entries.keys().next().value!)
}
