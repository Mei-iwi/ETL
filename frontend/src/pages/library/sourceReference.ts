export type SourceInfo = {
    ownership: 'SOURCE_BACKED' | 'LOCAL_OWNED'
    provider: string
    availability: 'UNKNOWN' | 'AVAILABLE' | 'UNAVAILABLE' | 'CHANGED'
    can_open_original: boolean
    original_url: string | null
}

/** Defense in depth: capability and URL must both come from the authorized API. */
export function originalSourceUrl(info: SourceInfo | null): string | null {
    if (!info?.can_open_original || info.ownership !== 'SOURCE_BACKED' || !info.original_url) return null
    try {
        const url = new URL(info.original_url)
        if (url.protocol !== 'https:' || url.hostname !== 'drive.google.com' || url.port || url.username || url.password
            || url.search || url.hash || !/^\/file\/d\/[A-Za-z0-9_-]{1,200}\/view$/.test(url.pathname)) return null
        return url.href
    } catch { return null }
}
