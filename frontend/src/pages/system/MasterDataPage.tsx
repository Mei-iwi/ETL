import { useEffect, useState } from 'react'
import { getCatalogPage } from '../../services/libraryLookupApi'
import styles from './MasterDataPage.module.css'

const catalogs = {
    providers: { label: 'Nhà cung cấp', path: '/api/v1/providers' },
    types: { label: 'Loại học liệu', path: '/api/v1/resource-types' },
    grades: { label: 'Khối lớp', path: '/api/v1/grade-levels' },
    subjects: { label: 'Môn học', path: '/api/v1/subjects' },
}

const pageSize = 7
type Kind = keyof typeof catalogs
type Result = { key: string; rows: string[][]; total: number; pages: number }

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
        void getCatalogPage<Record<string, string | null>>(catalogs[kind].path, page, controller.signal, { size: pageSize, query: term }).then(response => {
            if (controller.signal.aborted) return
            const pages = Math.max(1, response.pagination.total_pages)
            if (page > pages) { setPage(pages); return }
            const rows = response.items.map(row => kind === 'providers' ? [row.id, row.name, row.provider_type] :
                kind === 'types' ? [row.code, row.name] : kind === 'grades' ? [row.id, row.grade_name_vi, row.grade_code] :
                    [row.id, row.name, row.grade_level_id]).map(row => row.map(value => value || '—'))
            setData({ key, rows, pages, total: response.pagination.total })
            setError(null)
        }).catch(reason => {
            if (!controller.signal.aborted) setError({ key, message: reason instanceof Error ? reason.message : 'Không tải được danh mục.' })
        })
        return () => controller.abort()
    }, [kind, page, term, attempt, key])

    const result = data?.key === key ? data : null
    const currentError = error?.key === key ? error : null
    const loading = !result && !currentError
    const columns = ['Mã', 'Tên danh mục', ...(kind === 'types' ? [] : [kind === 'subjects' ? 'Mã khối lớp' : kind === 'providers' ? 'Loại nhà cung cấp' : 'Mã khối'])]

    function clearSearch() {
        setQuery('')
        setTerm('')
        setPage(1)
    }

    return <section className={styles.page} aria-labelledby="master-data-title">
        <header className={styles.header}>
            <h1 id="master-data-title">Dữ liệu danh mục</h1>
        </header>

        <form className={styles.searchForm} role="search" aria-label="Tìm trong danh mục" onSubmit={event => {
            event.preventDefault()
            setTerm(query.trim())
            setPage(1)
            setAttempt(n => n + 1)
        }}>
            <label className={styles.field}>
                <span>Nhóm danh mục</span>
                <select value={kind} onChange={event => {
                    setKind(event.target.value as Kind)
                    setPage(1)
                    setQuery('')
                    setTerm('')
                }}>
                    {(Object.keys(catalogs) as Kind[]).map(value => <option key={value} value={value}>{catalogs[value].label}</option>)}
                </select>
            </label>
            <label className={`${styles.field} ${styles.searchField}`}>
                <span>Từ khóa</span>
                <input type="search" maxLength={200} placeholder={`Tìm trong ${catalogs[kind].label.toLowerCase()}…`} value={query} onChange={event => setQuery(event.target.value)} />
            </label>
            <button className={styles.button} type="submit">Tìm kiếm</button>
            <button className={styles.secondaryButton} type="button" disabled={loading} onClick={() => setAttempt(n => n + 1)}>{loading ? 'Đang tải…' : 'Tải lại'}</button>
            {(query || term) && <button className={styles.clearButton} type="button" onClick={clearSearch}>Xóa lọc</button>}
        </form>

        <div className={styles.sectionHeader}>
            <h2>{catalogs[kind].label}</h2>
        </div>
        {currentError && <div className={styles.notice} role="alert">{currentError.message} <button type="button" className={styles.retryButton} onClick={() => setAttempt(n => n + 1)}>Thử lại</button></div>}
        <div className={styles.tableWrapper} tabIndex={0} role="region" aria-label={`Danh sách ${catalogs[kind].label.toLowerCase()}`} aria-busy={loading}>
            <table className={styles.table}>
                <thead><tr>{columns.map(column => <th key={column} scope="col">{column}</th>)}</tr></thead>
                <tbody>
                    {loading ? <tr><td colSpan={columns.length} className={styles.message} role="status">Đang tải danh mục…</td></tr> : currentError ? <tr><td colSpan={columns.length} className={styles.message}>Chưa tải được dữ liệu. Vui lòng thử lại.</td></tr> : result?.rows.length ? result.rows.map(row => (
                        <tr key={row[0]}>{row.map((cell, i) => <td key={i} className={i === 1 ? styles.title : undefined}>{i === 0 ? <span className={styles.code}>{cell}</span> : cell}</td>)}</tr>
                    )) : <tr><td colSpan={columns.length} className={styles.message} role="status">{term ? 'Không tìm thấy kết quả. Thử từ khóa khác hoặc xóa bộ lọc.' : 'Danh mục chưa có dữ liệu.'}</td></tr>}
                </tbody>
            </table>
        </div>
        {result && <>
            <div className={styles.pagination}>
                <nav aria-label="Phân trang danh mục">
                    <button className={styles.button} type="button" disabled={page === 1} onClick={() => setPage(page - 1)}>Trước</button>
                    <span>Trang {page} / {result.pages}</span>
                    <button className={styles.button} type="button" disabled={page === result.pages} onClick={() => setPage(page + 1)}>Sau</button>
                </nav>
            </div>
        </>}
    </section>
}
