import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router'
import { clearAuthRequests, setAuthMode } from './transport'

type Principal = { role: 'ADMIN'; mode: string; admin_feature_enabled: boolean }
type Status = 'loading' | 'authenticated' | 'anonymous' | 'unavailable'
type Auth = { status: Status; mode: string; principal?: Principal; refresh: () => Promise<void>; logout: () => Promise<void> }
const Context = createContext<Auth | undefined>(undefined)
export function AuthProvider({ children }: { children: ReactNode }) {
    const [status, setStatus] = useState<Status>('loading')
    const [mode, setMode] = useState('pending')
    const [principal, setPrincipal] = useState<Principal>()
    const revision = useRef(0)
    const expired = useCallback(() => { revision.current++; clearAuthRequests(); setPrincipal(undefined); setStatus('anonymous') }, [])
    const refresh = useCallback(async () => {
        const expected = ++revision.current
        try {
            const config = await fetch('/api/v1/auth/config')
            if (!config.ok) throw new Error('Auth unavailable')
            const value = await config.json()
            if (expected !== revision.current) return
            if (!['single_admin', 'local_demo', 'legacy_local'].includes(value.mode)) throw new Error('Invalid auth mode')
            setMode(value.mode); setAuthMode(value.mode)
            if (value.mode === 'legacy_local') { setPrincipal(undefined); setStatus('authenticated'); return }
            const response = await fetch('/api/v1/auth/me')
            if (expected !== revision.current) return
            if (response.status === 401) { expired(); return }
            if (!response.ok) throw new Error('Auth unavailable')
            const user = await response.json()
            if (expected !== revision.current) return
            if (user.role !== 'ADMIN') throw new Error('Invalid principal')
            setPrincipal(user); setStatus('authenticated')
        } catch { if (expected === revision.current) { setPrincipal(undefined); setStatus('unavailable') } }
    }, [expired])
    useEffect(() => {
        void refresh()
        window.addEventListener('etl:session-expired', expired)
        const channel = new BroadcastChannel('etl-auth-state')
        channel.onmessage = event => { if (event.data === 'logout') expired() }
        const restored = (event: PageTransitionEvent) => { if (event.persisted) { expired(); void refresh() } }
        window.addEventListener('pageshow', restored)
        return () => { window.removeEventListener('etl:session-expired', expired); window.removeEventListener('pageshow', restored); channel.close() }
    }, [refresh, expired])
    const logout = async () => {
        const response = await fetch('/api/v1/auth/logout', { method: 'POST' })
        if (!response.ok) throw new Error('Chưa thể đăng xuất. Vui lòng thử lại.')
        expired()
        const channel = new BroadcastChannel('etl-auth-state')
        channel.postMessage('logout'); channel.close()
    }
    return <Context value={{ status, mode, principal, refresh, logout }}>{children}</Context>
}
export function useAuth() {
    const auth = useContext(Context)
    if (!auth) throw new Error('AuthProvider required')
    return auth
}
export function safeReturnTo(value: string | null) {
    if (!value || !value.startsWith('/') || value.startsWith('//') || value.includes('\\') || [...value].some(character => character.charCodeAt(0) <= 32)) return '/overview/summary'
    const url = new URL(value, location.origin)
    return url.origin === location.origin && !url.pathname.startsWith('/login') ? url.pathname + url.search + url.hash : '/overview/summary'
}
export function AuthGuard() {
    const auth = useAuth(), location = useLocation()
    if (auth.status === 'loading') return <div role="status">Đang kiểm tra phiên đăng nhập…</div>
    if (auth.status === 'unavailable') return <div role="alert">Dịch vụ xác thực chưa khả dụng. <button onClick={() => void auth.refresh()}>Thử lại</button></div>
    if (auth.status === 'anonymous') return <Navigate to={`/login?returnTo=${encodeURIComponent(location.pathname + location.search + location.hash)}`} replace />
    return <Outlet />
}
