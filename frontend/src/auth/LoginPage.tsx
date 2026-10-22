import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate, useSearchParams } from 'react-router'
import { Eye, EyeOff, Mail, LockKeyhole, Check } from 'lucide-react'
import logoBook from '../assets/img/logoBook.png'
import { safeReturnTo, useAuth } from './AuthProvider'
import './login.css'

export function LoginPage() {
    const auth = useAuth(), [params] = useSearchParams()
    const [email, setEmail] = useState(''), [password, setPassword] = useState('')
    const [remember, setRemember] = useState(false), [show, setShow] = useState(false)
    const [busy, setBusy] = useState(false), [success, setSuccess] = useState(false), [error, setError] = useState('')
    const alert = useRef<HTMLDivElement>(null)
    const mounted = useRef(true)
    useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
    useEffect(() => { if (error) alert.current?.focus() }, [error])
    if (auth.status === 'authenticated') return <SignedInRedirect to={auth.principal?.must_change_password ? '/account/password' : safeReturnTo(params.get('returnTo'))} />
    async function submit(event: FormEvent) {
        event.preventDefault()
        if (busy || auth.status === 'unavailable' || auth.status === 'loading') return
        setBusy(true); setError('')
        try {
            const response = await fetch('/api/v1/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password, remember_me: remember }) })
            setPassword('')
            if (!response.ok) {
                const body = await response.json().catch(() => null)
                throw new Error(response.status === 429 ? 'Thử đăng nhập quá nhiều lần. Vui lòng chờ rồi thử lại.' :
                    response.status === 401 ? 'Email hoặc mật khẩu không đúng.' :
                        body?.detail?.code === 'AUTH_ADMIN_DISABLED' ? 'Chức năng quản trị đang tắt.' : 'Không thể đăng nhập lúc này. Vui lòng thử lại.')
            }
            setSuccess(true)
            if (!matchMedia('(prefers-reduced-motion: reduce)').matches) await new Promise(resolve => setTimeout(resolve, 650))
            if (mounted.current) await auth.refresh()
        } catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : 'Không thể kết nối dịch vụ.') }
        finally { if (mounted.current) { setBusy(false); setSuccess(false) } }
    }
    return <main className="etl-login">
        <div className="etl-login-orbit" aria-hidden="true" />
        <section className="etl-login-border" aria-labelledby="login-title"><div className="etl-login-card">
            <div className="etl-login-mark"><img src={logoBook} alt="Kho học liệu" width={96} height={96} /></div>
            <h1 id="login-title">Đăng nhập</h1>
            {auth.status === 'unavailable' && <div role="alert" className="etl-login-error">Dịch vụ xác thực chưa khả dụng. <button onClick={() => void auth.refresh()}>Thử lại</button></div>}
            {auth.status === 'loading' && <p role="status">Đang kiểm tra phiên đăng nhập…</p>}
            {error && <div role="alert" tabIndex={-1} ref={alert} className="etl-login-error">{error}</div>}
            <form onSubmit={submit} aria-busy={busy}>
                <label htmlFor="login-email">Email</label>
                <div className="etl-field"><Mail className="etl-field-icon" aria-hidden="true" /><input id="login-email" name="email" type="email" placeholder="name@company.com" autoComplete="username" required maxLength={254} value={email} onChange={event => setEmail(event.target.value)} disabled={busy} /></div>
                <label htmlFor="login-password">Mật khẩu</label>
                <div className="etl-field etl-password"><LockKeyhole className="etl-field-icon" aria-hidden="true" /><input id="login-password" name="password" type={show ? 'text' : 'password'} autoComplete="current-password" placeholder="••••••••" required maxLength={1024} value={password} onChange={event => setPassword(event.target.value)} disabled={busy} />
                    <button type="button" aria-label={show ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'} aria-pressed={show} onClick={() => setShow(!show)} disabled={busy}>{show ? <EyeOff /> : <Eye />}</button></div>
                <label className="etl-remember"><input type="checkbox" checked={remember} onChange={event => setRemember(event.target.checked)} disabled={busy} />Ghi nhớ đăng nhập</label>
                <button className={`etl-login-submit ${success ? 'etl-login-success' : ''}`} disabled={busy || auth.status === 'loading' || auth.status === 'unavailable'} type="submit">
                    {success ? <><Check aria-hidden="true" /> Đăng nhập thành công</> : busy ? 'Đang xác thực…' : 'Đăng nhập'}
                </button>
                <div role="status" aria-live="polite" className="etl-login-status">{success ? 'Đã xác thực, đang mở hệ thống.' : ''}</div>
            </form>
        </div></section>
    </main>
}

function SignedInRedirect({ to }: { to: string }) {
    const docs = /^\/docs\/?(?:[?#]|$)/.test(to)
    useEffect(() => { if (docs) window.location.replace(to) }, [docs, to])
    return docs ? <p role="status">Đang mở tài liệu API…</p> : <Navigate to={to} replace />
}
