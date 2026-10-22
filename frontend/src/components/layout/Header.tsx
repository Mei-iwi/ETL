import logoBook from '../../assets/img/logoBook.png'

import { NavLink } from 'react-router'
import { useEffect, useRef, useState } from 'react'
import { UserRound, LogOut, ChevronDown } from 'lucide-react'
import { canVisit, useAuth } from '../../auth/AuthProvider'
import { navagationItems } from '../../config/navigation'

export function Header() {
    const auth = useAuth()
    type ConnectionState = 'checking' | 'online' | 'offline'

    const [connectionState, setConnectionState] = useState<ConnectionState>('checking')

    useEffect(() => {
        const controller = new AbortController()

        async function checkHealth() {
            try {
                const response = await fetch('/health', {
                    signal: controller.signal,
                    headers: {
                        Accept: 'application/json',
                    },
                })

                if (!response.ok) {
                    throw new Error(`Health check failed: ${response.status}`)
                }

                const data: {
                    status: string
                    database: string
                } = await response.json()


                const isOnline = data.status === 'ok' && (data.database === undefined || data.database === 'ok')

                setConnectionState(isOnline ? 'online' : 'offline')

            } catch (e) {
                if (e instanceof DOMException && e.name === 'AbortError') {
                    return
                }

                setConnectionState('offline')
            }
        }

        checkHealth()

        const intervalId = window.setInterval(checkHealth, 30_000)

        return () => {
            controller.abort()
            window.clearInterval(intervalId)
        }
    }, [])

    const statusText = {
        checking: 'Đang kiểm tra...',
        online: 'Đã kết nối',
        offline: 'Mất kết nối',
    }[connectionState]

    return (
        <header className='app-header'>
            <NavLink className='header-brand' to='/' aria-label='Về trang tổng quan'>
                <img className='header-logo' src={logoBook} alt='' />
                <span>Kho học liệu số</span>
            </NavLink>

            <nav className='main-menu' aria-label='Menu chính'>
                {navagationItems.filter(item => canVisit(item.to, auth.principal) || item.children.some(child => canVisit(child.to, auth.principal))).map(({ to, label, icon: Icon, end }) => (
                    <NavLink
                        key={to}
                        to={to}
                        end={end}
                        className={({ isActive }) =>
                            `nav-item${isActive ? ' active' : ''}`
                        }
                    >
                        <Icon aria-hidden='true' />
                        <span>{label}</span>
                    </NavLink>
                ))}
            </nav>

            <div className={`connection-status ${connectionState}`} role='status' aria-live='polite'>
                <span className='status-dot' aria-hidden='true' />
                <span><strong>Trạng thái:</strong> <span className='isconect'>{statusText}</span></span>
            </div>
            <AccountMenu />
        </header>
    )
}

function AccountMenu() {
    const auth = useAuth()
    const menu = useRef<HTMLDetailsElement>(null)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState('')

    useEffect(() => {
        function closeOutside(event: PointerEvent) {
            if (event.target instanceof Node && !menu.current?.contains(event.target) && menu.current) {
                menu.current.open = false
            }
        }
        function closeOnEscape(event: KeyboardEvent) {
            if (event.key === 'Escape' && menu.current?.open) {
                menu.current.open = false
                menu.current.querySelector('summary')?.focus()
            }
        }
        document.addEventListener('pointerdown', closeOutside)
        document.addEventListener('keydown', closeOnEscape)
        return () => {
            document.removeEventListener('pointerdown', closeOutside)
            document.removeEventListener('keydown', closeOnEscape)
        }
    }, [])

    if (!auth.principal) return null
    async function logout() {
        if (busy) return
        setBusy(true)
        setError('')
        try { await auth.logout() }
        catch { setError('Chưa thể đăng xuất. Vui lòng thử lại.') }
        finally { setBusy(false) }
    }

    return <details className="header-account" ref={menu}>
        <summary className="header-account-trigger" aria-label="Tài khoản">
            <span className="header-account-avatar"><UserRound aria-hidden="true" /></span>
            <ChevronDown className="header-account-chevron" aria-hidden="true" />
        </summary>
        <div className="header-account-panel">
            <p className="header-account-name">{auth.principal.display_name || (auth.mode === 'local_demo' ? 'Quản trị thử nghiệm · localhost' : 'Quản trị viên')}</p>
            {auth.mode === 'db_users' && <NavLink to="/account/password">Đổi mật khẩu</NavLink>}
            {!auth.principal.admin_feature_enabled && <p className="header-account-note">Chức năng quản trị đang tắt</p>}
            {['single_admin', 'db_users', 'master_users'].includes(auth.mode) && <button className="header-account-logout" type="button" disabled={busy} onClick={() => void logout()}>
                <LogOut aria-hidden="true" />{busy ? 'Đang đăng xuất…' : 'Đăng xuất'}
            </button>}
            {error && <p className="header-account-error" role="alert">{error}</p>}
        </div>
    </details>
}
