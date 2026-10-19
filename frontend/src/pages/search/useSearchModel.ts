import { useEffect, useState } from 'react'
import { getSearchModelStatus, retrySearchModel } from '../../services/searchApi'
import type { SearchModelStatus } from '../../services/searchApi'

export function useSearchModel() {
    const [status, setStatus] = useState<SearchModelStatus>({ state: 'LOADING', ready: false })
    const [error, setError] = useState(false)
    const [attempt, setAttempt] = useState({ count: 0, retry: false })
    useEffect(() => {
        const controller = new AbortController()
        let timer: ReturnType<typeof setTimeout>
        async function poll() {
            if (controller.signal.aborted) return
            let delay = 3000
            try {
                const next = await getSearchModelStatus(controller.signal)
                if (controller.signal.aborted) return
                setStatus(next); setError(false)
                delay = next.state === 'LOADING' ? 1000 : 3000
            } catch {
                if (controller.signal.aborted) return
                setStatus({ state: 'FAILED', ready: false }); setError(true)
            }
            timer = setTimeout(() => { void poll() }, delay)
        }
        // StrictMode cleanup aborts its probe before issuing a GET.
        void Promise.resolve().then(async () => {
            if (controller.signal.aborted) return
            if (attempt.retry) {
                try {
                    const next = await retrySearchModel(controller.signal)
                    if (controller.signal.aborted) return
                    setStatus(next)
                } catch {
                    if (controller.signal.aborted) return
                    setError(true)
                }
            }
            await poll()
        })
        return () => { controller.abort(); clearTimeout(timer) }
    }, [attempt])
    return { ...status, error,
        refresh: () => setAttempt(value => ({ count: value.count + 1, retry: false })),
        retry: () => setAttempt(value => ({ count: value.count + 1, retry: true })) }
}
