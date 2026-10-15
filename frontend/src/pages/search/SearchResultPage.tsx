import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { searchMaterials } from '../../services/searchApi'
import type { SearchResponse } from '../../types/search'
import layout from '../library/LibraryPages.module.css'
import styles from './SearchPages.module.css'
import { readSearch, writeSearch } from './searchParams'

export function SearchResults({ params }: { params: URLSearchParams }) {
    const criteria = readSearch(params)
    const [, setParams] = useSearchParams()
    const [result, setResult] = useState<SearchResponse | null>(null)
    const [error, setError] = useState('')
    const [attempt, setAttempt] = useState(0)
    const encoded = params.toString()
    useEffect(() => {
        const request = readSearch(new URLSearchParams(encoded))
        if (!request.query) return
        const controller = new AbortController()
        // StrictMode cleans up its probe effect before this microtask starts a POST.
        void Promise.resolve().then(() => {
            if (controller.signal.aborted) return
            return searchMaterials(request, controller.signal)
        })
            .then(data => { if (data && !controller.signal.aborted) { setResult(data); setError('') } })
            .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Tìm kiếm thất bại.') })
        return () => controller.abort()
    }, [encoded, attempt])
    return <section className={styles.results} aria-label="Kết quả tìm kiếm" tabIndex={0}>
        <div className={styles.stack}>
            {!criteria.query ? <p role="status">Nhập truy vấn để tìm kiếm.</p> : error ?
                <div className={layout.card}><p role="alert" className={styles.error}>{error}</p>
                    <button className={layout.primaryButton} onClick={() => { setError(''); setResult(null); setAttempt(value => value + 1) }}>Thử lại</button></div> :
                !result ? <p role="status" className={styles.pending}>Đang tìm kiếm…</p> : <>
                    <div className={styles.resultSummary}>
                        <p role="status" className={styles.success}>{result.total} kết quả</p>
                        {result.access_scope === 'local_demo' && <p role="status" className={styles.metadata}>Tra cứu thử nghiệm nội bộ</p>}
                    </div>
                    {result.items.length === 0 && <div className={layout.card}>
                        <p>{result.message === 'PUBLIC_ACCESS_UNCONFIRMED' ? 'Chưa có học liệu đủ quyền truy xuất công khai.' :
                            result.message === 'NO_READY_INDEX' ? 'Chưa có học liệu lập chỉ mục sẵn sàng.' :
                            result.message === 'NO_ACCESSIBLE_CONTENT' ? 'Không có nội dung được phép truy xuất trong phạm vi hiện tại.' :
                            result.message === 'QUERY_NO_KEYWORDS' ? 'Truy vấn không có từ khóa phù hợp.' : 'Không tìm thấy học liệu phù hợp.'}</p>
                    </div>}
                    {result.items.map((item, index) => <article key={item.resource_id} className={styles.searchResult}>
                        <p className={styles.source}>{item.provider_name || item.resource_type_code} · Phiên bản {item.version_number}</p>
                        <div className={styles.resultHeader}><h2><Link to={`/library/${encodeURIComponent(item.resource_id)}?${new URLSearchParams({ versionId: item.resource_version_id, contextId: item.context_id, returnTo: `/search/query-methods?${params}` })}`}>{item.resource_title}</Link></h2></div>
                        <dl className={styles.searchFacts}>
                            <div><dt>Xếp hạng</dt><dd>#{(result.page - 1) * result.size + index + 1}</dd></div>
                            {Number.isFinite(item.score) && <div><dt>Điểm {criteria.method.toUpperCase()}</dt><dd>{item.score.toFixed(4)}</dd></div>}
                            {item.score_components.bow !== undefined && <div><dt>BoW</dt><dd>{item.score_components.bow.toFixed(4)}</dd></div>}
                            {item.score_components.vector !== undefined && <div><dt>Tương đồng Vector</dt><dd>{item.score_components.vector.toFixed(4)}</dd></div>}
                            <div><dt>ContentBag phù hợp</dt><dd>{item.matched_bag_count}</dd></div>
                            <div><dt>Chunk</dt><dd>{item.page_from != null ? `Trang ${item.page_from}${item.page_to != null && item.page_to !== item.page_from ? `–${item.page_to}` : ''}` : 'Đoạn phù hợp'}</dd></div>
                        </dl>
                        <div className={styles.keywords}><span>Keyword</span>{item.matched_keywords.length ? item.matched_keywords.map(keyword =>
                            <span className={styles.keyword} key={keyword}>{keyword}</span>) : <span className={styles.metadata}>Không có từ khóa trùng khớp</span>}</div>
                    </article>)}
                    {result.total_pages > 1 && <nav aria-label="Phân trang kết quả" className={styles.pagination}>
                        <button className={layout.primaryButton} disabled={result.page <= 1} onClick={() => setParams(writeSearch({ ...criteria, page: result.page - 1 }))}>Trước</button>
                        <span>Trang {result.page}/{result.total_pages}</span>
                        <button className={layout.primaryButton} disabled={result.page >= result.total_pages} onClick={() => setParams(writeSearch({ ...criteria, page: result.page + 1 }))}>Tiếp</button>
                    </nav>}
                </>}
        </div>
    </section>
}
