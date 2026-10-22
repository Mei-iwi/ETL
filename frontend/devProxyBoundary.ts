import type { IncomingMessage, ServerResponse } from 'node:http'

export function localProxyGuard(req: IncomingMessage, res: ServerResponse, next: () => void) {
    if (!/^\/(?:(?:api|admin|docs)(?:[/?]|$)|openapi\.json(?:\?|$))/.test(req.url || '')) { next(); return }
    const peer = req.socket.remoteAddress || ''
    const local = peer === '::1' || /^127\.\d+\.\d+\.\d+$/.test(peer) ||
        /^::ffff:127\.\d+\.\d+\.\d+$/.test(peer)
    // Socket peer, never a client-supplied X-Role/Forwarded header.
    if (!local) {
        res.writeHead(403, { 'Content-Type': 'application/json', 'Cache-Control': 'private, no-store' })
        res.end(JSON.stringify({ detail: 'Local Development proxy only' }))
        return
    }
    next()
}
