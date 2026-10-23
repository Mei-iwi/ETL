import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router'
import { clearAuthRequests, setAuthMode } from './transport'

export type Principal = { id?: string; role: 'ADMIN' | 'OPERATOR' | 'VIEWER'; mode: string; admin_feature_enabled: boolean;
    roles?: string[]; display_name?: string; email?: string; must_change_password?: boolean; permissions?: string[]; resource_ids?: string[] }
type Status = 'loading' | 'authenticated' | 'anonymous' | 'unavailable'
type Auth = { status: Status; mode: string; principal?: Principal; refresh: () => Promise<void>; logout: () => Promise<void> }
const Context = createContext<Auth | undefined>(undefined)
function identityScope(mode: string, user?: Principal) {
    return JSON.stringify([mode, user?.id, user?.role, user?.roles, user?.permissions, user?.resource_ids, user?.must_change_password])
}
export function AuthProvider({ children }: { children: ReactNode }) {
    const [status, setStatus] = useState<Status>('loading')
    const [mode, setMode] = useState('pending')
    const [principal, setPrincipal] = useState<Principal>()
    const revision = useRef(0)
    const currentScope = useRef('')
    const pendingCheck = useRef<AbortController | null>(null)
    const expired = useCallback(() => { revision.current++; pendingCheck.current?.abort(); clearAuthRequests(); setPrincipal(undefined); setStatus('anonymous') }, [])
    const refresh = useCallback(async () => {
        const expected = ++revision.current
        pendingCheck.current?.abort()
        const controller = new AbortController()
        pendingCheck.current = controller
        // One deadline includes config, session and response-body reads.
        const deadline = window.setTimeout(() => controller.abort(), 8000)
        try {
            const config = await fetch('/api/v1/auth/config', { signal: controller.signal })
            if (!config.ok) throw new Error('Auth unavailable')
            const value = await config.json()
            if (expected !== revision.current) return
            if (!['single_admin', 'db_users', 'master_users', 'local_demo', 'legacy_local'].includes(value.mode)) throw new Error('Invalid auth mode')
            setMode(value.mode); setAuthMode(value.mode)
            if (value.mode === 'legacy_local') { setPrincipal(undefined); setStatus('authenticated'); return }
            const response = await fetch('/api/v1/auth/me', { signal: controller.signal })
            if (expected !== revision.current) return
            if (response.status === 401) { expired(); return }
            if (value.mode === 'master_users' && response.status === 503) {
                const failure = await response.json().catch(() => null)
                if (['AUTH_MASTER_STALE', 'AUTH_MASTER_UNAVAILABLE'].includes(failure?.detail?.code)) {
                    if (expected === revision.current) expired()
                    return
                }
            }
            if (!response.ok) throw new Error('Auth unavailable')
            const user = await response.json()
            if (expected !== revision.current) return
            if (!['ADMIN', 'OPERATOR', 'VIEWER'].includes(user.role)) throw new Error('Invalid principal')
            if (['db_users', 'master_users'].includes(value.mode) && (!Array.isArray(user.roles) || user.roles.some((role: string) => !['admin', 'operator', 'viewer'].includes(role)))) throw new Error('Invalid roles')
            const nextScope = identityScope(value.mode, user)
            if (currentScope.current && currentScope.current !== nextScope) clearAuthRequests()
            currentScope.current = nextScope
            setPrincipal(user); setStatus('authenticated')
        } catch { if (expected === revision.current) { setPrincipal(undefined); setStatus('unavailable') } }
        finally {
            window.clearTimeout(deadline)
            if (pendingCheck.current === controller) pendingCheck.current = null
        }
    }, [expired])
    useEffect(() => {
        void refresh()
        window.addEventListener('etl:session-expired', expired)
        const channel = new BroadcastChannel('etl-auth-state')
        channel.onmessage = event => { if (event.data === 'logout') expired() }
        const restored = (event: PageTransitionEvent) => { if (event.persisted) { expired(); void refresh() } }
        window.addEventListener('pageshow', restored)
        const onFocus = () => { void refresh() }
        window.addEventListener('focus', onFocus)
        return () => { revision.current++; pendingCheck.current?.abort(); window.removeEventListener('etl:session-expired', expired); window.removeEventListener('pageshow', restored); window.removeEventListener('focus', onFocus); channel.close() }
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
    if (auth.principal?.must_change_password && location.pathname !== '/account/password') return <Navigate to="/account/password" replace />
    if (!canVisit(location.pathname, auth.principal)) return <Navigate to="/overview/summary" replace />
    return <Outlet key={identityScope(auth.mode, auth.principal)} />
}

export function canVisit(path: string, user?: Principal) {
    if (path === '/systems/users') return user?.mode === 'db_users' && user.role === 'ADMIN' && !user.must_change_password
    if (!user || !['db_users', 'master_users'].includes(user.mode)) return true
    if (user.must_change_password) return path === '/account/password'
    if (path === '/account/password' || path === '/account/profile' || path === '/' || path.startsWith('/overview') || path.startsWith('/search')) return true
    if (user.role === 'ADMIN') return true
    if (path === '/library' || path === '/library/list') return true
    const resource = /^\/library\/([^/]+)(?:\/versions)?$/.exec(path)
    // Detail URLs are read-only candidates. Backend checks public file rights;
    // management route names must never be treated as resource IDs.
    if (resource && !['add', 'processing', 'indexing', 'source-sync', 'source-files',
        'source-sync-errors', 'source-sync-blocked'].includes(resource[1])) return true
    if (user.role !== 'OPERATOR') return false
    const permissions = user.permissions || []
    if (path.startsWith('/library/source-')) return permissions.includes('sync')
    if (path === '/library/indexing') return permissions.includes('index')
    if (path === '/library/processing') return permissions.includes('process')
    if (path.startsWith('/experiments')) return permissions.includes('evaluation')
    return false
}
