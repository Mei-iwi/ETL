// URL/form state stays intact; result navigation must revisit live authority.
import type { SearchResponse } from '../../types/search'

// Compatibility API: no cached result/snippet without a server visibility epoch.
export function clearSearchViewCache() {}
export function readSearchView(_key: string, _now = Date.now()): SearchResponse | null { return null }
export function saveSearchView(_key: string, _value: SearchResponse, _now = Date.now()) {}
