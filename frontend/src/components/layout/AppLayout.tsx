import { Outlet, useLocation } from 'react-router'
import { Header } from './Header'
import { Submenu } from './Submenu'
import './AppLayout.css'

export function AppLayout() {
    const { pathname } = useLocation()
    const searchPage = pathname === '/search' || pathname.startsWith('/search/')
    return (
        <div className='app-shell'>
            <div className='app-main'>
                <Header />
                <Submenu />

                <main className={`app-content${searchPage ? ' app-content-search' : ''}`}>
                    <Outlet />
                </main>
            </div>
        </div>
    )
}
