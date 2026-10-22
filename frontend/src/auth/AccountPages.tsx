import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate } from 'react-router'
import { useAuth } from './AuthProvider'
import './accounts.css'

type Account = {
    id: string; email: string; display_name: string; roles: string[]; is_active: boolean;
    is_locked: boolean; must_change_password: boolean; permissions: string[]; resource_ids: string[]
}
const capabilities = ['sync', 'process', 'index', 'evaluation']
const labels: Record<string, string> = { sync: 'Đồng bộ SOURCE', process: 'OCR / xử lý', index: 'Index / Reindex', evaluation: 'Thực nghiệm' }

async function api(path: string, method = 'GET', body?: unknown) {
    const response = await fetch('/api/v1/admin/users' + path, {
        method,
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body)
    })
    const value = await response.json().catch(() => null)
    if (!response.ok) {
        const messages: Record<string, string> = {
            AUTH_LAST_ADMIN_REQUIRED: 'Phải giữ ít nhất một quản trị viên hoạt động.',
            AUTH_EMAIL_CONFLICT: 'Email đã tồn tại.', AUTH_USER_INPUT_INVALID: 'Thông tin chưa hợp lệ.', AUTH_FORBIDDEN: 'Bạn không có quyền thực hiện.'
        }
        throw new Error(messages[value?.detail?.code] || 'Không thể thực hiện. Kiểm tra quyền, dữ liệu và kết nối.')
    }
    return value
}

export function AccountManagementPage() {
    const auth = useAuth()
    const [items, setItems] = useState<Account[]>([]), [page, setPage] = useState(1), [total, setTotal] = useState(0)
    const [email, setEmail] = useState(''), [name, setName] = useState(''), [password, setPassword] = useState('')
    const [role, setRole] = useState('viewer'), [permissions, setPermissions] = useState<string[]>([]), [resourceIds, setResourceIds] = useState('')
    const [editing, setEditing] = useState<Account>(), [resetting, setResetting] = useState<Account>()
    const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('')
    const allowed = auth.mode === 'db_users' && auth.principal?.role === 'ADMIN'
    const load = useCallback(async () => {
        if (!allowed) return
        try { const data = await api(`?page=${page}&size=20`); setItems(data.items); setTotal(data.total) }
        catch (reason) { setError(reason instanceof Error ? reason.message : 'Không thể tải tài khoản.') }
    }, [page, allowed])
    useEffect(() => { void load() }, [load])
    if (!allowed) return <p role="alert">Quản lý tài khoản chỉ dành cho Admin trong chế độ Database.</p>
    async function action(work: () => Promise<unknown>) {
        if (busy) return
        setBusy(true); setError(''); setNotice('')
        try { await work(); setNotice('Đã lưu thay đổi.'); await load(); await auth.refresh() }
        catch (reason) { setError(reason instanceof Error ? reason.message : 'Không thể lưu.') }
        finally { setBusy(false); setPassword('') }
    }
    function edit(item?: Account) {
        setEditing(item); setResetting(undefined); setPassword(''); setEmail(item?.email || ''); setName(item?.display_name || '')
        setRole(item?.roles.includes('admin') ? 'admin' : item?.roles.includes('operator') ? 'operator' : 'viewer')
        setPermissions(item?.permissions || []); setResourceIds(item?.resource_ids.join(', ') || '')
    }
    function save(event: FormEvent) {
        event.preventDefault()
        const grants = {
            permissions: role === 'viewer' ? [] : permissions,
            resource_ids: resourceIds.split(/[\s,]+/).filter(Boolean)
        }
        void action(async () => {
            if (resetting) { await api(`/${resetting.id}/reset-password`, 'POST', { password }); setResetting(undefined) }
            else if (editing) {
                // Separate role endpoint preserves explicit server-side last-admin enforcement.
                const roleChanged = editing.roles.length !== 1 || editing.roles[0] !== role
                if (roleChanged) await api(`/${editing.id}/roles`, 'PATCH', { roles: [role] })
                if (!roleChanged || editing.id !== auth.principal?.id) await api(`/${editing.id}`, 'PATCH', { display_name: name, ...grants })
                edit()
            } else { await api('', 'POST', { email, display_name: name, password, roles: [role], ...grants }); edit() }
        })
    }
    return <section className="account-page">
        <h1>Tài khoản</h1>
        {error && <p role="alert" className="account-error">{error}</p>}
        {notice && <p role="status">{notice}</p>}
        <form className="account-form" onSubmit={save}>
            <h2>{resetting ? `Đặt lại mật khẩu: ${resetting.email}` : editing ? 'Cập nhật tài khoản' : 'Tạo tài khoản'}</h2>
            {!resetting && <>
                <label>Email<input type="email" value={email} onChange={e => setEmail(e.target.value)} required disabled={!!editing || busy} autoComplete="off" /></label>
                <label>Tên hiển thị<input value={name} onChange={e => setName(e.target.value)} required maxLength={120} disabled={busy} /></label>
                <label>Vai trò<select value={role} onChange={e => setRole(e.target.value)} disabled={busy}>
                    <option value="viewer">Viewer</option><option value="operator">Operator</option><option value="admin">Admin</option>
                </select></label>
                {role !== 'viewer' && <fieldset><legend>Quyền vận hành (Operator)</legend>{capabilities.map(code => <label key={code}>
                    <input type="checkbox" checked={permissions.includes(code)} disabled={busy} onChange={e => setPermissions(old => e.target.checked ? [...old, code] : old.filter(value => value !== code))} />{labels[code]}</label>)}</fieldset>}
                <label>Resource ID được cấp quyền (phân cách bằng dấu phẩy)<textarea value={resourceIds} onChange={e => setResourceIds(e.target.value)} disabled={busy} /></label>
            </>}
            {(!editing || resetting) && <label>Mật khẩu mới<input type="password" autoComplete="new-password" minLength={15} maxLength={1024} required value={password} onChange={e => setPassword(e.target.value)} disabled={busy} /></label>}
            {(!editing || resetting) && <p>Tài khoản phải đổi mật khẩu sau lần đăng nhập tiếp theo.</p>}
            <div className="account-actions"><button disabled={busy} type="submit">{busy ? 'Đang lưu…' : 'Lưu'}</button>
                {(editing || resetting) && <button type="button" disabled={busy} onClick={() => edit()}>Hủy</button>}</div>
        </form>
        <div className="account-table"><table><thead><tr><th>Email</th><th>Tên</th><th>Vai trò</th><th>Trạng thái</th><th>Thao tác</th></tr></thead>
            <tbody>{items.map(item => <tr key={item.id}><td>{item.email}</td><td>{item.display_name}</td><td>{item.roles.join(', ')}</td>
                <td>{!item.is_active ? 'Vô hiệu hóa' : item.is_locked ? 'Đã khóa' : 'Hoạt động'}{item.must_change_password ? ' · Cần đổi mật khẩu' : ''}</td>
                <td><div className="account-actions"><button disabled={busy} onClick={() => edit(item)}>Sửa / phân quyền</button>
                    <button disabled={busy} onClick={() => void action(() => api(`/${item.id}`, 'PATCH', { is_locked: !item.is_locked }))}>{item.is_locked ? 'Mở khóa' : 'Khóa'}</button>
                    <button disabled={busy} onClick={() => void action(() => api(`/${item.id}`, 'PATCH', { is_active: !item.is_active }))}>{item.is_active ? 'Vô hiệu hóa' : 'Kích hoạt'}</button>
                    <button disabled={busy} onClick={() => { setResetting(item); setEditing(undefined); setPassword('') }}>Đặt lại mật khẩu</button></div></td></tr>)}</tbody></table></div>
        <div className="account-actions"><button disabled={busy || page <= 1} onClick={() => setPage(page - 1)}>Trước</button><span>Trang {page} · {total} tài khoản</span>
            <button disabled={busy || page * 20 >= total} onClick={() => setPage(page + 1)}>Sau</button><button disabled={busy} onClick={() => void load()}>Tải lại</button></div>
    </section>
}

export function ChangePasswordPage() {
    const auth = useAuth()
    const [old, setOld] = useState(''), [password, setPassword] = useState(''), [confirmation, setConfirmation] = useState('')
    const [busy, setBusy] = useState(false), [error, setError] = useState('')
    if (auth.mode !== 'db_users') return <Navigate to="/overview/summary" replace />
    async function submit(event: FormEvent) {
        event.preventDefault()
        if (password !== confirmation) { setError('Mật khẩu xác nhận không khớp.'); return }
        setBusy(true); setError('')
        try {
            const response = await fetch('/api/v1/auth/change-password', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ old_password: old, new_password: password })
            })
            if (!response.ok) throw new Error(response.status === 429 ? 'Thử quá nhiều lần. Vui lòng thử lại sau.' : 'Không thể đổi mật khẩu. Kiểm tra mật khẩu hiện tại và thử lại.')
            await auth.refresh()
        } catch (reason) { setError(reason instanceof Error ? reason.message : 'Không thể đổi mật khẩu.') }
        finally { setBusy(false); setOld(''); setPassword(''); setConfirmation('') }
    }
    return <section className="account-page"><h1>Đổi mật khẩu</h1>{error && <p role="alert" className="account-error">{error}</p>}
        {auth.principal?.must_change_password && <p>Bạn cần đổi mật khẩu trước khi tiếp tục.</p>}
        <form className="account-form" onSubmit={submit}>
            <label>Mật khẩu hiện tại<input type="password" autoComplete="current-password" value={old} onChange={e => setOld(e.target.value)} required disabled={busy} /></label>
            <label>Mật khẩu mới<input type="password" autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} required minLength={15} maxLength={1024} disabled={busy} /></label>
            <label>Nhập lại mật khẩu mới<input type="password" autoComplete="new-password" value={confirmation} onChange={e => setConfirmation(e.target.value)} required minLength={15} maxLength={1024} disabled={busy} /></label>
            <button disabled={busy}>{busy ? 'Đang lưu…' : 'Đổi mật khẩu và đăng nhập lại'}</button>
        </form></section>
}
