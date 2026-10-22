import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { ArrowUpRight, FileText, SearchX } from 'lucide-react'
import { searchMaterials, SearchApiError } from '../../services/searchApi'
import type { SearchResponse } from '../../types/search'
import layout from '../library/LibraryPages.module.css'
import styles from './SearchPages.module.css'
import { readSearch, writeSearch } from './searchParams'
import { canVisit, useAuth } from '../../auth/AuthProvider'
import { SearchContext } from './SearchContext'

export function SearchResults({ params, onBusyChange }: { params: URLSearchParams; onBusyChange?: (busy: boolean) => void }) {
    const { principal } = useAuth()
    const criteria = readSearch(params)
    const [, setParams] = useSearchParams()
    const [result, setResult] = useState<SearchResponse | null>(null)
    const [error, setError] = useState('')
    const [attempt, setAttempt] = useState(0)
    const retrying = useRef(false)
    const navigating = useRef(false)
    function goToPage(page: number) {
        // React may batch rapid clicks before unmounting this page. Only one
        // history entry/request belongs to this transition.
        if (navigating.current) return
        navigating.current = true
        setParams(writeSearch({ ...criteria, page }))
    }
    const encoded = params.toString()
    useEffect(() => {
        const request = readSearch(new URLSearchParams(encoded))
        if (!request.query) return
        const controller = new AbortController()
        // StrictMode cleans up its probe effect before this microtask starts a POST.
        void Promise.resolve().then(() => {
            if (controller.signal.aborted) return
            onBusyChange?.(true)
            return searchMaterials(request, controller.signal)
        })
            .then(data => { if (data && !controller.signal.aborted) { setResult(data); setError('') } })
            .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof SearchApiError ? reason.message : 'Tìm kiếm thất bại. Vui lòng thử lại.') })
            .finally(() => { if (!controller.signal.aborted) { retrying.current = false; onBusyChange?.(false) } })
        return () => { controller.abort(); onBusyChange?.(false) }
    }, [encoded, attempt, onBusyChange])
    return <section className={styles.results} aria-label="Kết quả tìm kiếm" aria-busy={Boolean(criteria.query && !error && !result)} tabIndex={0}>
        <div className={styles.stack}>
            {!criteria.query ? <p role="status">Nhập truy vấn để tìm kiếm.</p> : error ?
                <div className={layout.card}><p role="alert" className={styles.error}>{error}</p>
                    <button className={layout.primaryButton} onClick={() => { if (retrying.current) return; retrying.current = true; setError(''); setResult(null); setAttempt(value => value + 1) }}>Thử lại</button></div> :
                !result ? <div role="status" className={styles.loading}><span className={styles.spinner} aria-hidden="true" /><strong>Đang tìm kiếm…</strong></div> : <>
                    <div className={styles.resultSummary}>
                        <div><h2>Kết quả tìm kiếm</h2><p role="status"><strong>{result.total.toLocaleString('vi-VN')}</strong> kết quả cho <strong>“{criteria.query}”</strong></p></div>
                        {result.items.length > 0 && <span className={styles.metadata}>Phù hợp nhất trước · {Math.min((result.page - 1) * result.size + 1, result.total)}–{Math.min((result.page - 1) * result.size + result.items.length, result.total)} / {result.total}</span>}
                        {result.access_scope === 'local_demo' && <p role="status" className={styles.metadata}>Tra cứu thử nghiệm nội bộ</p>}
                    </div>
                    {result.items.length === 0 && <div className={styles.emptyState}>
                        <SearchX size={32} aria-hidden="true" /><h3>Chưa tìm thấy học liệu</h3>
                        <p>{result.message === 'PUBLIC_ACCESS_UNCONFIRMED' ? 'Chưa có học liệu đủ quyền truy xuất công khai.' :
                            result.message === 'NO_READY_INDEX' ? 'Chưa có học liệu lập chỉ mục sẵn sàng.' :
                            result.message === 'NO_ACCESSIBLE_CONTENT' ? 'Không có nội dung được phép truy xuất trong phạm vi hiện tại.' :
                            result.message === 'QUERY_NO_KEYWORDS' ? 'Truy vấn không có từ khóa phù hợp.' : 'Không tìm thấy học liệu phù hợp.'}</p>
                    </div>}
                    {result.items.map((item, index) => <article key={item.resource_id} className={styles.searchResult}>
                        <div className={styles.resultTopline}><span className={styles.documentType}><FileText size={15} aria-hidden="true" /> Học liệu</span><span className={styles.source}>{item.provider_name || item.resource_type_code} · Phiên bản {item.version_number}</span></div>
                        <div className={styles.resultHeader}><h2>{item.can_view_file !== false && canVisit(`/library/${encodeURIComponent(item.resource_id)}`, principal)
                            ? <Link to={`/library/${encodeURIComponent(item.resource_id)}?${new URLSearchParams({ versionId: item.resource_version_id, contextId: item.context_id, returnTo: `/search/query-methods?${params}` })}`}>{item.resource_title}<ArrowUpRight size={18} aria-hidden="true" /></Link>
                            : item.resource_title}</h2></div>
                        {item.bag_title && <p className={styles.sectionTitle}>{item.bag_title}</p>}
                        {item.snippet && <p className={styles.snippet}>{item.snippet}</p>}
                        <SearchContext id={item.context_id} />
                        {item.page_from != null && <p className={styles.metadata}>Nội dung liên quan tại trang {item.page_from}{item.page_to != null && item.page_to !== item.page_from ? `–${item.page_to}` : ''}</p>}
                        {item.matched_keywords.length > 0 && <div className={styles.keywords}><span>Từ khóa liên quan</span>{item.matched_keywords.map(keyword =>
                            <span className={styles.keyword} key={keyword}>{keyword}</span>)}</div>}
                        <details className={styles.more}><summary>Chi tiết xếp hạng</summary><dl className={styles.searchFacts}>
                            <div><dt>Xếp hạng</dt><dd>#{(result.page - 1) * result.size + index + 1}</dd></div>
                            {Number.isFinite(item.score) && <div><dt>Điểm {criteria.method.toUpperCase()}</dt><dd>{item.score.toFixed(4)}</dd></div>}
                            {item.score_components.bow !== undefined && <div><dt>BoW</dt><dd>{item.score_components.bow.toFixed(4)}</dd></div>}
                            {item.score_components.vector !== undefined && <div><dt>Tương đồng Vector</dt><dd>{item.score_components.vector.toFixed(4)}</dd></div>}
                            <div><dt>Phần nội dung phù hợp</dt><dd>{item.matched_bag_count}</dd></div>
                        </dl></details>
                    </article>)}
                    {result.total_pages > 1 && <nav aria-label="Phân trang kết quả" className={styles.pagination}>
                        <button className={layout.primaryButton} disabled={result.page <= 1} onClick={() => goToPage(result.page - 1)}>Trước</button>
                        <span>Trang {result.page}/{result.total_pages}</span>
                        <button className={layout.primaryButton} disabled={result.page >= result.total_pages} onClick={() => goToPage(result.page + 1)}>Tiếp</button>
                    </nav>}
                </>}
        </div>
    </section>
}
