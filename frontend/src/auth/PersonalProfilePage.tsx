import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { ArrowLeft, Check, Mail, MapPin, Pencil, Building2, BookOpen } from 'lucide-react'
import avatar from '../assets/img/default-avatar.svg'
import { getPersonalProfile, ProfileApiError, updatePersonalProfile } from '../services/profileApi'
import type { PersonalProfile } from '../services/profileApi'
import './profile.css'

const date = (value: string | null) => value && Number.isFinite(Date.parse(value))
    ? new Intl.DateTimeFormat('vi-VN', { dateStyle: 'medium' }).format(new Date(value)) : 'Chưa cập nhật'

export function PersonalProfilePage() {
    const navigate = useNavigate()
    const [profile, setProfile] = useState<PersonalProfile>()
    const [loading, setLoading] = useState(true), [editing, setEditing] = useState(false), [saving, setSaving] = useState(false)
    const [error, setError] = useState(''), [success, setSuccess] = useState(''), [conflict, setConflict] = useState(false)
    const [organization, setOrganization] = useState(''), [address, setAddress] = useState(''), [bio, setBio] = useState('')
    const lifetime = useRef<AbortController | null>(null)
    const saveInFlight = useRef(false)
    const alert = useRef<HTMLParagraphElement>(null)

    function apply(value: PersonalProfile) {
        setProfile(value); setOrganization(value.organization_name || ''); setAddress(value.address || ''); setBio(value.bio || '')
    }
    async function load(signal: AbortSignal) {
        setLoading(true); setError(''); setSuccess('')
        try {
            const value = await getPersonalProfile(signal)
            if (!signal.aborted) { apply(value); setConflict(false); setEditing(false) }
        } catch (e) { if (!signal.aborted) setError(e instanceof ProfileApiError ? e.message : 'Chưa thể tải hồ sơ. Vui lòng thử lại.') }
        finally { if (!signal.aborted) setLoading(false) }
    }
    useEffect(() => {
        const controller = new AbortController(); lifetime.current = controller
        void load(controller.signal)
        return () => { controller.abort() }
    }, [])
    useEffect(() => { if (error) alert.current?.focus() }, [error])

    async function save(event: FormEvent) {
        event.preventDefault()
        if (!profile || !profile.editable || saveInFlight.current || conflict || !lifetime.current) return
        saveInFlight.current = true
        setSaving(true); setError(''); setSuccess('')
        const signal = lifetime.current.signal
        try {
            const updated = await updatePersonalProfile({ revision: profile.revision,
                organization_name: organization.trim() || null, address: address.trim() || null, bio: bio.trim() || null }, signal)
            if (!signal.aborted) { apply(updated); setEditing(false); setSuccess('Đã cập nhật thông tin cá nhân.') }
        } catch (e) {
            if (!signal.aborted) {
                setError(e instanceof ProfileApiError ? e.message : 'Chưa thể lưu thông tin. Vui lòng thử lại.')
                setConflict(e instanceof ProfileApiError && e.code === 'PROFILE_CHANGED')
            }
        } finally { saveInFlight.current = false; if (!signal.aborted) setSaving(false) }
    }
    const reload = () => { if (lifetime.current) void load(lifetime.current.signal) }
    const back = () => { if (window.history.state?.idx > 0) navigate(-1); else navigate('/overview/summary') }

    return <section className="personal-profile" aria-label="Thông tin cá nhân">
        <div className="profile-toolbar"><button type="button" onClick={back} className="profile-back"><ArrowLeft size={17} />Quay lại</button></div>
        {loading ? <div className="profile-loading" role="status">Đang tải thông tin cá nhân…</div> : <>
            {error && <div className="profile-notice"><p ref={alert} tabIndex={-1} role="alert">{error}</p>
                <button type="button" onClick={reload} disabled={saving}>Tải lại hồ sơ</button></div>}
            {success && <p className="profile-success" role="status"><Check size={17} />{success}</p>}
            {profile && <div className="profile-stage">
                <aside className="profile-card">
                    <div className="profile-card-body"><img className="profile-avatar" src={avatar} alt="Ảnh đại diện mặc định" />
                        <h2>{profile.full_name}</h2><span className="profile-accent" />
                        <p className="profile-role">{profile.role_name}</p>
                    </div>
                    <div className="profile-card-footer"><BookOpen size={17} /><span>Kho học liệu số</span></div>
                </aside>
                <div className="profile-details">
                    <span className="profile-eyebrow">THÔNG TIN CÁ NHÂN</span>
                    <h1 id="profile-title">Xin chào</h1>
                    <p className="profile-intro">{profile.full_name}</p>
                    {profile.editable && <div className="profile-actions"><button type="button" className="profile-primary" disabled={editing}
                        onClick={() => { setEditing(true); setSuccess(''); setError('') }}><Pencil size={15} />Cập nhật thông tin</button>
                    </div>}
                    {!profile.editable && <p className="profile-muted">Tài khoản này chưa liên kết với hồ sơ đồng bộ để chỉnh sửa.</p>}
                    <dl className="profile-identity"><div><dt>Email</dt><dd><Mail size={15} />{profile.email || 'Chưa có email'}</dd></div>
                        <div><dt>Vai trò</dt><dd>{profile.role_name}</dd></div>
                        {profile.email_verified !== null && <div><dt>Xác minh email</dt><dd>{profile.email_verified ? 'Đã xác minh' : 'Chưa xác minh'}</dd></div>}
                        {profile.subject_name && <div><dt>Môn học</dt><dd>{profile.subject_name}</dd></div>}
                    </dl>
                    {editing ? <form className="profile-form" onSubmit={save}>
                        <p className="profile-muted">Họ tên, email và vai trò được đồng bộ và chỉ dùng để xem.</p>
                        <label htmlFor="profile-organization">Cơ quan / Đơn vị</label>
                        <input autoFocus id="profile-organization" maxLength={255} value={organization} onChange={e => setOrganization(e.target.value)} disabled={saving} />
                        <label htmlFor="profile-address">Địa chỉ</label>
                        <textarea id="profile-address" rows={2} maxLength={1000} value={address} onChange={e => setAddress(e.target.value)} disabled={saving} />
                        <label htmlFor="profile-bio">Giới thiệu</label>
                        <textarea id="profile-bio" rows={4} maxLength={2000} value={bio} onChange={e => setBio(e.target.value)} disabled={saving} />
                        <div className="profile-form-actions"><button className="profile-primary" type="submit" disabled={saving || conflict}>{saving ? 'Đang lưu…' : 'Lưu thay đổi'}</button>
                            <button className="profile-secondary" type="button" disabled={saving} onClick={() => { apply(profile); setEditing(false); setError(''); setConflict(false) }}>Hủy</button></div>
                    </form> : <div className="profile-about"><h3>Giới thiệu</h3><p>{profile.bio || 'Chưa có giới thiệu.'}</p>
                        <div className="profile-contact"><Building2 size={17} /><div><span>Cơ quan / Đơn vị</span><p>{profile.organization_name || 'Chưa cập nhật'}</p></div></div>
                        <div className="profile-contact"><MapPin size={17} /><div><span>Địa chỉ</span><p>{profile.address || 'Chưa cập nhật'}</p></div></div></div>}
                    <div className="profile-dates"><span>Tham gia: {date(profile.created_at)}</span><span>Cập nhật: {date(profile.updated_at)}</span></div>
                </div>
            </div>}
        </>}
    </section>
}
