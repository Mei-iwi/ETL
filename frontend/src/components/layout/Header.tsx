import logoBook from '../../assets/img/logoBook.png'

import { NavLink } from 'react-router'
import { useEffect, useState } from 'react'
import { navagationItems } from '../../config/navigation'

export function Header() {
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


                const isOnline = data.status === 'ok' && data.database === 'ok'

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
                {navagationItems.map(({ to, label, icon: Icon, end }) => (
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
        </header>
    )
}
