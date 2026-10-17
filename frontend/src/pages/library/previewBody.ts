export const PREVIEW_MAX_BYTES = 30 * 1024 * 1024

/** Count actual response bytes, even if Content-Length is absent or dishonest. */
export async function readPreviewBlob(response: Response, signal?: AbortSignal, maxBytes = PREVIEW_MAX_BYTES): Promise<Blob> {
    const declared = response.headers.get('Content-Length')
    if (declared && /^\d+$/.test(declared) && Number(declared) > maxBytes) {
        await response.body?.cancel()
        throw new Error('Tệp lớn hơn 30 MB. Hãy tải về để xem.')
    }
    if (!response.body) throw new Error('Không đọc được nội dung tệp.')
    const reader = response.body.getReader()
    const chunks: BlobPart[] = []
    let received = 0
    try {
        for (;;) {
            signal?.throwIfAborted()
            const { done, value } = await reader.read()
            signal?.throwIfAborted()
            if (done) break
            received += value.byteLength
            if (received > maxBytes) throw new Error('Tệp lớn hơn 30 MB. Hãy tải về để xem.')
            chunks.push(new Uint8Array(value).buffer)
        }
        return new Blob(chunks, { type: response.headers.get('Content-Type') || 'application/octet-stream' })
    } catch (error) {
        await reader.cancel().catch(() => {})
        throw error
    } finally {
        reader.releaseLock()
    }
}
