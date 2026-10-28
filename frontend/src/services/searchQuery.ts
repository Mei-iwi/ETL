/** NFC and whitespace normalization shared by URL, UI and response validation. */
export function normalizeSearchQuery(value: string): string {
    return value.normalize('NFC').replace(/\s+/gu, ' ').trim()
}
