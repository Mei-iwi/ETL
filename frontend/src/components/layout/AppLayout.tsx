import { Outlet, useLocation } from 'react-router'
import { Header } from './Header'
import { Submenu } from './Submenu'
import './AppLayout.css'

export function AppLayout() {
    const { pathname } = useLocation()
    const searchPage = pathname === '/search' || pathname.startsWith('/search/')
    const profilePage = pathname === '/account/profile'
    return (
        <div className='app-shell'>
            <div className={`app-main${profilePage ? ' app-main-profile' : ''}`}>
                <Header />
                {!profilePage && <Submenu />}

                <main className={`app-content${searchPage ? ' app-content-search' : ''}${profilePage ? ' app-content-profile' : ''}`}>
                    <Outlet />
                </main>
            </div>
        </div>
    )
}
