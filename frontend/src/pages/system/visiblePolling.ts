// One request generation per active view. Hidden views abort and release their timer.
export function startVisiblePolling(
    load: (signal: AbortSignal) => Promise<void>,
    busy: () => boolean,
    visibility: Pick<Document, 'hidden' | 'addEventListener' | 'removeEventListener'> = document,
    timing: { busy: number; idle: number | null } = { busy: 5000, idle: 45000 },
) {
    let stopped = false
    let controller: AbortController | undefined
    let timer: ReturnType<typeof setTimeout> | undefined
    function cancel() {
        if (timer !== undefined) clearTimeout(timer)
        controller?.abort()
    }
    async function refresh() {
        cancel()
        if (stopped || visibility.hidden) return
        const current = new AbortController()
        controller = current
        try { await load(current.signal) }
        catch (error) { if (!current.signal.aborted) throw error }
        finally {
            const delay = busy() ? timing.busy : timing.idle
            if (delay !== null && !stopped && !current.signal.aborted && controller === current && !visibility.hidden)
                timer = setTimeout(() => { void refresh() }, delay)
        }
    }
    function changed() {
        if (visibility.hidden) cancel()
        else void refresh()
    }
    visibility.addEventListener('visibilitychange', changed)
    void refresh()
    return { refresh, stop() {
        stopped = true
        cancel()
        visibility.removeEventListener('visibilitychange', changed)
    } }
}
