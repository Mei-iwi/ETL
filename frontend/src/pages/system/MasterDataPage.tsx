import { useEffect, useState } from 'react'
import { catalogMessage, getCatalogPage } from '../../services/libraryLookupApi'
import layout from '../library/LibraryPages.module.css'
import styles from './SystemMasterSyncPage.module.css'

const catalogs = {
    providers: { label: 'Nhà cung cấp', path: '/api/v1/providers' },
    types: { label: 'Loại học liệu', path: '/api/v1/resource-types' },
    grades: { label: 'Khối lớp', path: '/api/v1/grade-levels' },
    subjects: { label: 'Môn học', path: '/api/v1/subjects' },
}
type Kind = keyof typeof catalogs
type Result = { key: string; rows: string[][]; total: number; pages: number; notice: string }

export function MasterDataPage() {
    const [kind, setKind] = useState<Kind>('providers')
    const [data, setData] = useState<Result | null>(null)
    const [error, setError] = useState<{ key: string; message: string } | null>(null)
    const [query, setQuery] = useState('')
    const [term, setTerm] = useState('')
    const [page, setPage] = useState(1)
    const [attempt, setAttempt] = useState(0)
    const key = JSON.stringify([kind, page, term, attempt])
    useEffect(() => {
        const controller = new AbortController()
        void getCatalogPage<Record<string, string | null>>(catalogs[kind].path, page, controller.signal, { size: 7, query: term }).then(response => {
            if (controller.signal.aborted) return
            const pages = Math.max(1, response.pagination.total_pages)
            if (page > pages) { setPage(pages); return }
            const rows = response.items.map(row => kind === 'providers' ? [row.id, row.name, row.provider_type] :
                kind === 'types' ? [row.code, row.name, '—'] : kind === 'grades' ? [row.id, row.grade_name_vi, row.grade_code] :
                    [row.id, row.name, row.grade_level_id]).map(row => row.map(value => value || '—'))
            const notice = kind === 'subjects' || ['ALL_SOURCE_PLUS_LOCAL', 'MASTER_MANAGED'].includes(response.catalog?.completeness ?? '')
                ? '' : catalogMessage(response.catalog)
            setData({ key, rows, pages, total: response.pagination.total, notice }); setError(null)
        }).catch(reason => {
            if (!controller.signal.aborted) setError({ key, message: reason instanceof Error ? reason.message : 'Không tải được danh mục.' })
        })
        return () => controller.abort()
    }, [kind, page, term, attempt, key])
    const result = data?.key === key ? data : null
    return <section className={styles.page}>
        <h1>Danh mục học liệu</h1>
        <section className={styles.card}>
            <form className={layout.actions} style={{ alignItems: 'flex-end' }} onSubmit={event => {
                event.preventDefault(); setTerm(query.trim()); setPage(1); setAttempt(n => n + 1)
            }}>
                <label className={layout.field}>Nhóm danh mục<select value={kind} onChange={event => {
                    setKind(event.target.value as Kind); setPage(1); setQuery(''); setTerm('')
                }}>{Object.entries(catalogs).map(([value, item]) => <option key={value} value={value}>{item.label}</option>)}</select></label>
                <label className={layout.field}>Lọc danh mục<input maxLength={200} value={query} onChange={event => setQuery(event.target.value)} /></label>
                <button className={layout.primaryButton}>Lọc</button>
                <button type="button" className={layout.primaryButton} onClick={() => setAttempt(n => n + 1)}>Tải lại</button>
            </form>
            {result?.notice && <p role="status" className={styles.notice}>{result.notice}</p>}
            {error?.key === key ? <p role="alert" className={layout.alert}>{error.message}</p> :
                !result ? <p role="status">Đang tải danh mục…</p> :
                    <><div className={styles.tableWrap}><table className={styles.fileTable}>
                        <thead><tr><th>Mã</th><th>Tên</th><th>{kind === 'subjects' ? 'Khối lớp (ID)' : kind === 'providers' ? 'Loại' : kind === 'grades' ? 'Mã khối' : 'Thông tin'}</th></tr></thead>
                        <tbody>{result.rows.map(row => <tr key={row[0]}>{row.map((cell, i) => <td key={i}>{cell}</td>)}</tr>)}</tbody>
                    </table></div>{result.total === 0 && <p>Không có danh mục phù hợp trong kết quả hiện có.</p>}
                        <div className={styles.pagination}>
                            <button className={styles.button} disabled={page === 1} onClick={() => setPage(page - 1)}>Trước</button>
                            <span>Trang {page}/{result.pages}</span>
                            <button className={styles.button} disabled={page === result.pages} onClick={() => setPage(page + 1)}>Sau</button>
                        </div></>}
        </section>
    </section>
}
