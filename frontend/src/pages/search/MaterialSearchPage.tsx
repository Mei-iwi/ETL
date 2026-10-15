import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { getGradeLevels, getResourceTypes, getSubjects } from '../../services/libraryLookupApi'
import type { GradeLevelOption, ResourceTypeOption, SubjectOption } from '../../services/libraryLookupApi'
import type { SearchMethod } from '../../types/search'
import layout from '../library/LibraryPages.module.css'
import styles from './SearchPages.module.css'
import { methods, readSearch, writeSearch } from './searchParams'
import { SearchResults } from './SearchResultPage'

export function MaterialSearchPage() {
    const [params] = useSearchParams()
    // Keying the form restores criteria after back/forward, without setState in an effect.
    return <SearchForm key={params.toString()} params={params} />
}

function SearchForm({ params }: { params: URLSearchParams }) {
    const initial = readSearch(params)
    const navigate = useNavigate()
    const [query, setQuery] = useState(initial.query)
    const [method, setMethod] = useState<SearchMethod>(initial.method)
    const [filters, setFilters] = useState(initial.filters)
    const [catalog, setCatalog] = useState<{ subjects: SubjectOption[]; grades: GradeLevelOption[]; types: ResourceTypeOption[] } | null>(null)
    const [catalogError, setCatalogError] = useState('')
    const [error, setError] = useState('')
    useEffect(() => {
        const controller = new AbortController()
        void Promise.all([getSubjects(controller.signal), getGradeLevels(controller.signal), getResourceTypes(controller.signal)])
            .then(([subjects, grades, types]) => { if (!controller.signal.aborted) setCatalog({ subjects, grades, types }) })
            .catch(() => { if (!controller.signal.aborted) setCatalogError('Không tải được bộ lọc. Có thể tìm không lọc.') })
        return () => controller.abort()
    }, [])
    function submit(event: FormEvent) {
        event.preventDefault()
        const normalized = query.normalize('NFC').trim()
        if (!normalized || normalized.length > 300) { setError('Nhập truy vấn từ 1 đến 300 ký tự.'); return }
        navigate(`/search/query-methods?${writeSearch({ query: normalized, method, filters, page: 1, size: 5 })}`)
    }
    return <section className={`${layout.page} ${styles.searchPage}`}>
        <header className={`${layout.pageHeader} ${styles.pageHeader}`}><h1>Tra cứu học liệu</h1></header>
        <form className={`${layout.card} ${layout.form} ${styles.searchForm}`} onSubmit={submit}>
            <fieldset className={styles.methodPicker}><legend className={styles.visuallyHidden}>Phương pháp truy vấn</legend>
                {methods.map(item => <button key={item.value} type="button" aria-pressed={method === item.value}
                    className={method === item.value ? styles.methodActive : styles.methodButton}
                    onClick={() => setMethod(item.value)}>{item.label}</button>)}
            </fieldset>
            <div className={styles.searchBar}><div className={layout.field}><label htmlFor="search-query" className={styles.visuallyHidden}>Tìm kiếm học liệu</label>
                <input id="search-query" value={query} onChange={event => setQuery(event.target.value)} maxLength={300} placeholder="Nhập nội dung cần tìm…" required /></div>
                <button type="submit" className={layout.primaryButton}>Tìm kiếm</button></div>
            <details className={styles.filterPanel}><summary>Bộ lọc{Object.values(filters).filter(Boolean).length > 0 && ` (${Object.values(filters).filter(Boolean).length})`}</summary><div className={styles.filterBody}><div className={styles.filters}>
                <div className={layout.field}><label htmlFor="search-grade">Khối lớp</label>
                    <select id="search-grade" disabled={!catalog} value={filters.grade_level_id || ''}
                        onChange={event => setFilters({ ...filters, grade_level_id: event.target.value || null, subject_id: null })}>
                        <option value="">Tất cả</option>{catalog?.grades.map(item => <option key={item.id} value={item.id}>{item.grade_name_vi}</option>)}
                    </select></div>
                <div className={layout.field}><label htmlFor="search-subject">Môn học</label>
                    <select id="search-subject" disabled={!catalog} value={filters.subject_id || ''}
                        onChange={event => setFilters({ ...filters, subject_id: event.target.value || null })}>
                        <option value="">Tất cả</option>{catalog?.subjects.filter(item => !filters.grade_level_id || item.grade_level_id === filters.grade_level_id)
                            .map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
                    </select></div>
                <div className={layout.field}><label htmlFor="search-type">Loại học liệu</label>
                    <select id="search-type" disabled={!catalog} value={filters.resource_type_code || ''}
                        onChange={event => setFilters({ ...filters, resource_type_code: event.target.value || null })}>
                        <option value="">Tất cả</option>{catalog?.types.map(item => <option key={item.code} value={item.code}>{item.name}</option>)}
                    </select></div>
            </div>
            <div className={layout.actions}>
                <button type="button" className={layout.secondaryButton} onClick={() => setFilters({})}>Xóa bộ lọc</button></div>
            </div>
            </details>
            {catalogError && <p role="status" className={styles.pending}>{catalogError}</p>}
            {error && <p role="alert" className={styles.error}>{error}</p>}
        </form>
        {initial.query && <SearchResults key={params.toString()} params={params} />}
    </section>
}
