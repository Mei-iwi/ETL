import { useEffect, useState } from 'react'
import { getProviders, getResourceTypes, getGradeLevels, getSubjects } from '../../services/libraryLookupApi'
import layout from '../library/LibraryPages.module.css'
import styles from './SystemMasterSyncPage.module.css'

const catalogs = {
    providers: {
        label: 'Nhà cung cấp', source: 'providers → master_learning_resources.provider_*',
        load: async (signal: AbortSignal) => (await getProviders(signal)).map(row => [row.id, row.name, row.provider_type])
    },
    types: {
        label: 'Loại học liệu', source: 'resource_types → master_learning_resources.resource_type_*',
        load: async (signal: AbortSignal) => (await getResourceTypes(signal)).map(row => [row.code, row.name, '—'])
    },
    grades: {
        label: 'Khối lớp', source: 'grade_levels + education_levels → master_grade_levels',
        load: async (signal: AbortSignal) => (await getGradeLevels(signal)).map(row => [row.id, row.grade_name_vi, row.grade_code])
    },
    subjects: {
        label: 'Môn học', source: 'subjects → subjects',
        load: async (signal: AbortSignal) => (await getSubjects(signal)).map(row => [row.id, row.name, row.grade_level_id || '—'])
    },
}
type Kind = keyof typeof catalogs

export function MasterDataPage() {
    const [kind, setKind] = useState<Kind>('providers')
    const [data, setData] = useState<{ kind: Kind; rows: string[][] } | null>(null)
    const [error, setError] = useState<{ kind: Kind; message: string } | null>(null)
    const [query, setQuery] = useState('')
    const [page, setPage] = useState(1)
    const [attempt, setAttempt] = useState(0)
    useEffect(() => {
        const controller = new AbortController()
        void catalogs[kind].load(controller.signal).then(rows => {
            if (!controller.signal.aborted) { setData({ kind, rows }); setError(null) }
        }).catch(reason => {
            if (!controller.signal.aborted) {
                setError({ kind, message: reason instanceof Error ? reason.message : 'Không tải được danh mục.' })
            }
        })
        return () => controller.abort()
    }, [kind, attempt])
    const rows = data?.kind === kind ? data.rows.filter(row => row.join(' ').toLocaleLowerCase('vi').includes(query.toLocaleLowerCase('vi'))) : []
    const totalPages = Math.max(1, Math.ceil(rows.length / 7))
    const currentPage = Math.min(page, totalPages)
    return <section className={styles.page}>
        <h1>Master Data</h1>
        <section className={styles.card}>
            <h2>Danh mục chỉ đọc</h2>
            <div className={layout.actions} style={{ alignItems: 'flex-end' }}>
                <label className={layout.field}>Nhóm danh mục<select value={kind} onChange={event => {
                    setKind(event.target.value as Kind); setPage(1); setQuery('')
                }}>{Object.entries(catalogs).map(([key, item]) => <option key={key} value={key}>{item.label}</option>)}</select></label>
                <label className={layout.field}>Lọc danh mục<input value={query} onChange={event => { setQuery(event.target.value); setPage(1) }} /></label>
                <button className={layout.primaryButton} onClick={() => setAttempt(n => n + 1)}>Tải lại</button>
            </div>
            {error?.kind === kind ? <p role="alert" className={layout.alert}>{error.message}</p> :
                data?.kind !== kind ? <p role="status">Đang tải danh mục…</p> :
                    <><div className={styles.tableWrap}><table className={styles.fileTable}>
                        <thead><tr><th>Mã</th><th>Tên</th><th>{kind === 'subjects' ? 'Khối lớp (ID)' : kind === 'providers' ? 'Loại' : kind === 'grades' ? 'Mã khối' : 'Thông tin'}</th></tr></thead>
                        <tbody>{rows.slice((currentPage - 1) * 7, currentPage * 7).map(row => <tr key={row[0]}>{row.map((cell, i) => <td key={i}>{cell}</td>)}</tr>)}</tbody>
                    </table></div>{rows.length === 0 && <p>Không có danh mục phù hợp.</p>}
                        <div className={styles.pagination}>
                            <button className={styles.button} disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>Trước</button>
                            <span>Trang {currentPage}/{totalPages}</span>
                            <button className={styles.button} disabled={currentPage === totalPages} onClick={() => setPage(currentPage + 1)}>Sau</button>
                        </div></>}
        </section>
    </section>
}

