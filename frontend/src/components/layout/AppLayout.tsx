import { Outlet, useLocation } from 'react-router'
import { Header } from './Header'
import { Submenu } from './Submenu'
import { RouteContent } from '../RouteContent'
import './AppLayout.css'

export function AppLayout() {
    const { pathname, search } = useLocation()
    const searchPage = pathname === '/search' || pathname.startsWith('/search/')
    const profilePage = pathname === '/account/profile'
    return (
        <div className='app-shell'>
            <div className={`app-main${profilePage ? ' app-main-profile' : ''}`}>
                <Header />
                {!profilePage && <Submenu />}

                <main className={`app-content${searchPage ? ' app-content-search' : ''}${profilePage ? ' app-content-profile' : ''}`}>
                    <RouteContent resetKey={pathname + search}>
                        <Outlet />
                    </RouteContent>
                </main>
            </div>
        </div>
    )
}
