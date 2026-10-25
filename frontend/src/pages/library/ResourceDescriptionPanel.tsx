import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Pencil } from 'lucide-react'
import styles from './LibraryResourceDetailPage.module.css'

type Author = { name: string; organization: string | null; role: string | null }
type Description = {
    resource_id: string; description: string | null; authors: Author[] | null; tags: string[] | null
    revision: string; editable: boolean; unavailable_reason: string | null
}

export function ResourceDescriptionPanel({ resourceId, children }: { resourceId: string; children: ReactNode }) {
    const [data, setData] = useState<Description | null>(null)
    const [description, setDescription] = useState(''), [tags, setTags] = useState('')
    const [authors, setAuthors] = useState<Author[]>([])
    const [editing, setEditing] = useState(false), [saving, setSaving] = useState(false)
    const [error, setError] = useState(''), [notice, setNotice] = useState(''), [conflict, setConflict] = useState(false)
    const lifetime = useRef<AbortController | null>(null)
    const inFlight = useRef(false)
    const url = `/api/v1/resources/${encodeURIComponent(resourceId)}/description`

    function apply(value: Description) {
        setData(value); setDescription(value.description || '')
        setTags((value.tags || []).join(', ')); setAuthors(value.authors || [])
        setEditing(false); setConflict(false)
    }

    async function load(signal?: AbortSignal) {
        setError(''); setNotice('')
        try {
            const response = await fetch(url, { signal, cache: 'no-store' })
            if (!response.ok) throw new Error('Chưa thể tải thông tin bổ sung. Vui lòng thử lại.')
            const value: Description = await response.json()
            if (!signal?.aborted) apply(value)
        } catch (e) { if (!signal?.aborted) setError(e instanceof Error ? e.message : 'Không thể tải thông tin.') }
    }

    useEffect(() => {
        const controller = new AbortController(); lifetime.current = controller
        void load(controller.signal)
        return () => controller.abort()
        // A resource change starts a fresh request and discards the previous editor.
    }, [resourceId])

    async function save(event: FormEvent) {
        event.preventDefault()
        if (!data?.editable || inFlight.current || conflict) return
        const signal = lifetime.current?.signal
        inFlight.current = true; setSaving(true); setError(''); setNotice('')
        try {
            const response = await fetch(url, { method: 'PATCH', signal,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ revision: data.revision, description: description.trim() || null,
                    tags: tags.split(',').map(value => value.trim()).filter(Boolean), authors }) })
            if (response.status === 409) {
                setConflict(true)
                throw new Error('Thông tin đã thay đổi. Hãy tải lại trước khi cập nhật.')
            }
            if (!response.ok) throw new Error(response.status === 422
                ? 'Kiểm tra thông tin nhập: tác giả cần có tên, tối đa 50 tác giả và 50 thẻ.'
                : 'Không thể cập nhật thông tin. Vui lòng kiểm tra quyền hoặc thử lại.')
            const value: Description = await response.json()
            if (!signal?.aborted) { apply(value); setNotice('Đã cập nhật thông tin học liệu.') }
        } catch (e) { if (!signal?.aborted) setError(e instanceof Error ? e.message : 'Không thể cập nhật.') }
        finally { inFlight.current = false; if (!signal?.aborted) setSaving(false) }
    }

    return <section className={`${styles.panel} ${styles.informationPanel}`} aria-labelledby="resource-description-title">
        <div className={styles.descriptionHeading}>
            <h2 id="resource-description-title">Thông tin chung</h2>
            {data?.editable && !editing && <button type="button" className={styles.secondaryButton}
                onClick={() => { setEditing(true); setNotice('') }}><Pencil size={14} aria-hidden="true" />Cập nhật thông tin</button>}
        </div>
        {children}
        <div className={styles.descriptionFields}>
        {error && <p className={styles.error} role="alert">{error}</p>}
        {notice && <p role="status" className={styles.ready}>{notice}</p>}
        {(error || conflict) && <button type="button" disabled={saving} className={styles.secondaryButton}
            onClick={() => void load(lifetime.current?.signal)}>Tải lại thông tin</button>}
        {!data && !error && <p className={styles.empty}>Đang tải thông tin bổ sung...</p>}
        {data?.unavailable_reason && <p className={styles.empty}>Học liệu chưa được liên kết với thông tin bổ sung.</p>}
        {data && !data.unavailable_reason && (editing ? <form onSubmit={event => void save(event)} className={styles.descriptionForm}>
            <fieldset disabled={saving}>
                <label>Mô tả<textarea maxLength={10000} rows={5} value={description} onChange={e => setDescription(e.target.value)} /></label>
                <label>Thẻ học liệu (cách nhau bằng dấu phẩy)<input maxLength={5000} value={tags} onChange={e => setTags(e.target.value)} /></label>
                <div className={styles.descriptionHeading}><h3>Tác giả</h3>
                    <button type="button" disabled={authors.length >= 50} className={styles.secondaryButton}
                        onClick={() => setAuthors([...authors, { name: '', organization: null, role: null }])}>Thêm tác giả</button></div>
                {authors.map((author, i) => <div key={i} className={styles.authorEditor}>
                    <label>Họ tên<input required maxLength={255} value={author.name} onChange={e => setAuthors(authors.map((a, n) => n === i ? { ...a, name: e.target.value } : a))} /></label>
                    <label>Đơn vị<input maxLength={255} value={author.organization || ''} onChange={e => setAuthors(authors.map((a, n) => n === i ? { ...a, organization: e.target.value || null } : a))} /></label>
                    <label>Vai trò<input maxLength={255} value={author.role || ''} onChange={e => setAuthors(authors.map((a, n) => n === i ? { ...a, role: e.target.value || null } : a))} /></label>
                    <button type="button" className={styles.secondaryButton} aria-label={`Bỏ tác giả ${i + 1}`}
                        onClick={() => setAuthors(authors.filter((_, n) => n !== i))}>Bỏ</button>
                </div>)}
                <div className={styles.descriptionActions}>
                    <button type="submit" className={styles.historyLink} disabled={conflict}>{saving ? 'Đang lưu...' : 'Lưu thay đổi'}</button>
                    <button type="button" className={styles.secondaryButton} onClick={() => apply(data)}>Hủy</button>
                </div>
            </fieldset>
        </form> : <dl className={styles.details}>
            <div className={styles.fullWidth}><dt>Mô tả</dt><dd className={styles.descriptionText}>{data.description || 'Chưa có mô tả.'}</dd></div>
            <div><dt>Tác giả</dt><dd>{data.authors?.length ? <ul className={styles.authorList}>{data.authors.map((author, i) =>
                <li key={i}>{[author.name, author.organization, author.role].filter(Boolean).join(' · ')}</li>)}</ul> : 'Chưa có thông tin tác giả.'}</dd></div>
            <div><dt>Thẻ học liệu</dt><dd>{data.tags?.length ? <ul className={styles.tagList}>{data.tags.map(tag =>
                <li key={tag} className={styles.tag}>{tag}</li>)}</ul> : 'Chưa có thẻ học liệu.'}</dd></div>
        </dl>)}
        </div>
    </section>
}
