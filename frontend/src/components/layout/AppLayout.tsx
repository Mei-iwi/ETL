import { Outlet } from 'react-router'
import { Header } from './Header'
import { Submenu } from './Submenu'
import './AppLayout.css'

export function AppLayout() {
    return (
        <div className='app-shell'>
            <div className='app-main'>
                <Header />
                <Submenu />

                <main className='app-content'>
                    <Outlet />
                </main>
            </div>
        </div>
    )
}