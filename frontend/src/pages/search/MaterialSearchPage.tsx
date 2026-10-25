import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { BookOpen, Search, SlidersHorizontal, X } from 'lucide-react'
import { getGradeLevels, getResourceTypes, getSubjects } from '../../services/libraryLookupApi'
import type { GradeLevelOption, ResourceTypeOption, SubjectOption } from '../../services/libraryLookupApi'
import type { SearchMethod } from '../../types/search'
import layout from '../library/LibraryPages.module.css'
import styles from './SearchPages.module.css'
import { methods, readSearch, writeSearch } from './searchParams'
import { SearchResults } from './SearchResultPage'
import { useSearchModel } from './useSearchModel'
import { canVisit, useAuth } from '../../auth/AuthProvider'

export function MaterialSearchPage() {
    const [params] = useSearchParams()
    const model = useSearchModel()
    // Page navigation keeps the form/catalog mounted. A criteria change starts a
    // new criteria session. Recent authorized page snapshots are retained in tab memory.
    const session = writeSearch({ ...readSearch(params), page: 1 }).toString()
    return <SearchForm key={session} params={params} model={model} />
}

function SearchForm({ params, model }: { params: URLSearchParams; model: ReturnType<typeof useSearchModel> }) {
    const { principal } = useAuth()
    const initial = readSearch(params)
    const navigate = useNavigate()
    const [query, setQuery] = useState(initial.query)
    const [method, setMethod] = useState<SearchMethod>(initial.method)
    const [filters, setFilters] = useState(initial.filters)
    const [catalog, setCatalog] = useState<{ subjects: SubjectOption[]; grades: GradeLevelOption[]; types: ResourceTypeOption[] } | null>(null)
    const [catalogError, setCatalogError] = useState('')
    const [error, setError] = useState('')
    const [busy, setBusy] = useState(false)
    const submitting = useRef(false)
    const filterCount = Object.values(filters).filter(Boolean).length
    const selectedFilters = [
        { key: 'grade_level_id' as const, label: catalog?.grades.find(item => item.id === filters.grade_level_id)?.grade_name_vi || 'Khối lớp đã chọn' },
        { key: 'subject_id' as const, label: catalog?.subjects.find(item => item.id === filters.subject_id)?.name || 'Môn học đã chọn' },
        { key: 'resource_type_code' as const, label: catalog?.types.find(item => item.code === filters.resource_type_code)?.name || filters.resource_type_code },
        { key: 'resource_format' as const, label: filters.resource_format?.toUpperCase() },
        { key: 'resource_id' as const, label: 'Trong học liệu đã chọn' },
        { key: 'resource_version_id' as const, label: 'Trong phiên bản đã chọn' },
    ].filter(item => filters[item.key])
    const hasChanges = Boolean(initial.query) && writeSearch({ ...initial, page: 1 }).toString() !==
        writeSearch({ ...initial, query, method, filters, page: 1 }).toString()
    useEffect(() => {
        const controller = new AbortController()
        void Promise.all([getSubjects(controller.signal), getGradeLevels(controller.signal), getResourceTypes(controller.signal)])
            .then(([subjects, grades, types]) => { if (!controller.signal.aborted) setCatalog({ subjects, grades, types }) })
            .catch(() => { if (!controller.signal.aborted) setCatalogError('Không tải được bộ lọc. Có thể tìm không lọc.') })
        return () => controller.abort()
    }, [])
    function submit(event: FormEvent) {
        event.preventDefault()
        if (busy || submitting.current || (method !== 'bow' && !model.ready)) return
        const normalized = query.normalize('NFC').trim()
        if (!normalized || normalized.length > 300) { setError('Nhập nội dung cần tìm, tối đa 300 ký tự.'); return }
        const next = writeSearch({ query: normalized, method, filters, page: 1, size: 5 }).toString()
        if (next === params.toString()) return
        submitting.current = true
        navigate(`/search/query-methods?${next}`)
    }
    return <section className={`${layout.page} ${styles.searchPage}`}>
        <header className={`${layout.pageHeader} ${styles.pageHeader}`}>
            <h1>Tra cứu học liệu</h1>
        </header>
        <form role="search" aria-label="Tra cứu học liệu" className={`${layout.card} ${layout.form} ${styles.searchForm}`} onSubmit={submit}>
            <div className={styles.searchBar}><div className={`${layout.field} ${styles.queryField}`}><label htmlFor="search-query" className={styles.visuallyHidden}>Tìm kiếm học liệu</label>
                <Search size={20} aria-hidden="true" className={styles.searchIcon} />
                <input id="search-query" type="search" value={query} onChange={event => { setQuery(event.target.value); setError('') }} maxLength={300} placeholder="Nhập từ khóa hoặc câu hỏi…" aria-describedby={error ? 'search-error' : undefined} aria-invalid={Boolean(error)} required /></div>
                <button type="submit" className={layout.primaryButton} disabled={busy || (method !== 'bow' && !model.ready)}>{busy ? 'Đang tìm…' : 'Tìm kiếm'}</button></div>
            <div className={styles.searchOptions}>
                <fieldset className={styles.methodPicker}><legend className={styles.visuallyHidden}>Cách tìm kiếm</legend>
                    {methods.map(item => <button key={item.value} type="button" aria-pressed={method === item.value}
                        className={method === item.value ? styles.methodActive : styles.methodButton}
                        disabled={item.value !== 'bow' && !model.ready}
                        onClick={() => setMethod(item.value)}>{item.label}</button>)}
                </fieldset>
            <details className={styles.filterPanel}><summary><SlidersHorizontal size={16} aria-hidden="true" /> Bộ lọc{filterCount > 0 && <span className={styles.filterCount}>{filterCount}</span>}</summary><div className={styles.filterBody}><div className={styles.filters}>
                <div className={layout.field}><label htmlFor="search-grade">Khối lớp</label>
                    <select id="search-grade" disabled={!catalog} value={filters.grade_level_id || ''}
                        onChange={event => setFilters({ ...filters, grade_level_id: event.target.value || null, subject_id: null })}>
                        <option value="">Tất cả khối lớp</option>{catalog?.grades.map(item => <option key={item.id} value={item.id}>{item.grade_name_vi}</option>)}
                    </select></div>
                <div className={layout.field}><label htmlFor="search-subject">Môn học</label>
                    <select id="search-subject" disabled={!catalog} value={filters.subject_id || ''}
                        onChange={event => setFilters({ ...filters, subject_id: event.target.value || null })}>
                        <option value="">Tất cả môn học</option>{catalog?.subjects.filter(item => !filters.grade_level_id || item.grade_level_id === filters.grade_level_id)
                            .map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
                    </select></div>
                <div className={layout.field}><label htmlFor="search-type">Loại học liệu</label>
                    <select id="search-type" disabled={!catalog} value={filters.resource_type_code || ''}
                        onChange={event => setFilters({ ...filters, resource_type_code: event.target.value || null })}>
                        <option value="">Tất cả loại học liệu</option>{catalog?.types.map(item => <option key={item.code} value={item.code}>{item.name}</option>)}
                    </select></div>
                <div className={layout.field}><label htmlFor="search-format">Định dạng</label>
                    <select id="search-format" value={filters.resource_format || ''}
                        onChange={event => setFilters({ ...filters, resource_format: event.target.value || null })}>
                        <option value="">Tất cả định dạng</option>{['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv', 'png', 'jpg', 'jpeg', 'mp3', 'mp4'].map(format =>
                            <option key={format} value={format}>{format.toUpperCase()}</option>)}
                    </select></div>
            </div>
            <div className={layout.actions}>
                <button type="button" className={styles.textButton} disabled={!filterCount} onClick={() => setFilters({})}><X size={14} aria-hidden="true" /> Xóa bộ lọc</button></div>
            </div>
            </details>
            </div>
            {selectedFilters.length > 0 && <div className={styles.selectedFilters} aria-label="Bộ lọc đã chọn">{selectedFilters.map(item =>
                <button type="button" key={item.key} aria-label={`Bỏ bộ lọc: ${item.label}`} onClick={() => setFilters({ ...filters, [item.key]: null, ...(item.key === 'grade_level_id' ? { subject_id: null } : {}) })}>{item.label}<X size={13} aria-hidden="true" /></button>)}</div>}
            {hasChanges && <p role="status" className={styles.methodHelp}>Nhấn Tìm kiếm để áp dụng thay đổi.</p>}
            {catalogError && <p role="status" className={styles.pending}>{catalogError}</p>}
            {!model.ready && <div role="status" className={model.state === 'FAILED' || model.state === 'STOPPED' ? styles.error : styles.pending}>
                {model.error ? 'Chưa kiểm tra được trạng thái tìm kiếm. Có thể chọn BoW.' :
                    model.state === 'FAILED' || model.state === 'STOPPED' ? 'Chưa thể chuẩn bị tìm kiếm. Có thể chọn BoW.' : 'Đang chuẩn bị tìm kiếm…'}
                {(model.state !== 'FAILED' || canVisit('/library/indexing', principal)) && <>{' '}<button type="button" className={styles.textButton} onClick={model.state === 'FAILED' ? model.retry : model.refresh}>
                    {model.state === 'FAILED' ? 'Thử lại' : 'Kiểm tra lại'}</button></>}
            </div>}
            {error && <p id="search-error" role="alert" className={styles.error}>{error}</p>}
        </form>
        {initial.query && (initial.method === 'bow' || model.ready) && <SearchResults key={params.toString()} params={params} onBusyChange={setBusy} />}
        {!initial.query && <div className={styles.welcome}>
            <div className={styles.welcomeIcon}><BookOpen size={30} aria-hidden="true" /></div>
            <h2>Bạn muốn tìm hiểu điều gì?</h2>
            <div className={styles.suggestions}><span>Thử tìm với</span>{['Phương trình bậc hai', 'Định luật Newton', 'Cách viết bài văn nghị luận'].map(example => <button key={example} type="button" onClick={() => { setQuery(example); document.getElementById('search-query')?.focus() }}>{example}</button>)}</div>
        </div>}
    </section>
}
