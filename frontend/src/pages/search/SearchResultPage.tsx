import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { searchMaterials } from '../../services/searchApi'
import type { SearchResponse } from '../../types/search'
import layout from '../library/LibraryPages.module.css'
import styles from './SearchPages.module.css'
import { readSearch, writeSearch } from './searchParams'

export function SearchResultpage() {
    const [params] = useSearchParams()
    return <SearchResults key={params.toString()} params={params} />
}

function SearchResults({ params }: { params: URLSearchParams }) {
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
    return <section className={layout.page}>
        <header className={layout.pageHeader}><h1>Kết quả tìm kiếm</h1></header>
        <div className={styles.stack}>
            <div className={`${layout.card} ${styles.resultHeader}`}>
                <span>{criteria.query || 'Chưa nhập truy vấn'} · {criteria.method.toUpperCase()}</span>
                <Link to={`/search/materials?${params}`} className={layout.primaryButton}>Sửa tìm kiếm</Link>
            </div>
            {!criteria.query ? <p role="status">Nhập truy vấn để tìm kiếm.</p> : error ?
                <div className={layout.card}><p role="alert" className={styles.error}>{error}</p>
                    <button className={layout.primaryButton} onClick={() => { setError(''); setResult(null); setAttempt(value => value + 1) }}>Thử lại</button></div> :
                !result ? <p role="status" className={styles.pending}>Đang tìm kiếm…</p> : <>
                    <p role="status" className={styles.success}>{result.total} học liệu · {result.ranking_algorithm}</p>
                    {result.items.length === 0 && <div className={layout.card}>
                        <p>{result.message === 'NO_READY_INDEX' ? 'Chưa có học liệu được lập chỉ mục và đủ điều kiện truy xuất.' :
                            result.message === 'QUERY_NO_KEYWORDS' ? 'Truy vấn không có từ khóa phù hợp.' : 'Không tìm thấy học liệu phù hợp.'}</p>
                        {result.message === 'NO_READY_INDEX' && <Link to="/library/indexing">Phân đoạn và chỉ mục</Link>}
                    </div>}
                    {result.items.map(item => <article key={item.resource_id} className={layout.card}>
                        <div className={styles.resultHeader}><h2><Link to={`/library/${encodeURIComponent(item.resource_id)}`}>{item.resource_title}</Link></h2>
                            <span>Điểm {item.score.toFixed(4)}</span></div>
                        <p className={styles.metadata}>Phiên bản {item.version_number} · {item.provider_name || item.resource_type_code} · {item.representation_mode}
                            {' · '}{item.matched_bag_count} ContentBag phù hợp</p>
                        <p className={styles.metadata}>{Object.entries(item.score_components).map(([key, value]) => `${key}: ${value?.toFixed(4)}`).join(' · ')}</p>
                        {item.matched_keywords.length > 0 && <p>Từ khóa: {item.matched_keywords.join(', ')}</p>}
                        <p className={styles.snippet}>{item.snippet}</p>
                        <p className={styles.metadata}>Trang {item.page_from ?? '—'}–{item.page_to ?? '—'} · {item.content_unit_id}</p>
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
