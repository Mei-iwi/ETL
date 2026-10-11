import { NavLink, useLocation } from "react-router";
import { navagationItems } from '../../config/navigation'

export function Submenu() {
    const { pathname } = useLocation()

    const activeMainMenu = navagationItems.find((menu) =>
        pathname === menu.to || pathname.startsWith(`${menu.to}/`)
    )

    if (!activeMainMenu) {
        return null
    }

    return (
        <nav className="sub-menu" aria-label={`Menu ${activeMainMenu.label}`}>
            {activeMainMenu.children.map((item) => (
                <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    className={({ isActive }) => `sub-menu-item${isActive ? ' active' : ''}`}
                >{item.label}</NavLink>
            ))}
        </nav >
    )

}