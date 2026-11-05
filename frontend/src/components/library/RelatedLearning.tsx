import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { useAuth } from '../../auth/AuthProvider'
import { identityScope } from '../../auth/authScope'
import { loadRelatedLearningState, type RelatedLearningState } from '../../services/relatedLearningApi'
import styles from './RelatedLearning.module.css'

export function RelatedLearning({ resourceId }: { resourceId: string }) {
    const { mode, principal } = useAuth()
    const scope = identityScope(mode, principal)
    const [result, setResult] = useState<RelatedLearningState & { resourceId: string; scope: string } | null>(null)
    useEffect(() => {
        const controller = new AbortController()
        void loadRelatedLearningState(resourceId, controller.signal).then(state => {
            if (!controller.signal.aborted) setResult({ resourceId, scope, ...state })
        })
        return () => controller.abort()
    }, [resourceId, scope])
    const current = result?.resourceId === resourceId && result.scope === scope ? result : null
    const items = current?.items || []
    if (current && ['disabled', 'unauthorized'].includes(current.status)) return null
    if (current?.status === 'ready' && !items.length) return null
    return <section className={styles.panel} aria-label="Học liệu liên quan">
        <h2>Học liệu liên quan</h2>
        {!current && <p role="status">Đang tải học liệu liên quan…</p>}
        {current?.status === 'unavailable' && <p role="status">Chưa thể tải học liệu liên quan.</p>}
        <ul aria-label="Danh sách học liệu liên quan">
            {items.map(item => <li key={item.resource_id}>
                <Link to={item.url}>{item.title}</Link> — <span>{item.reason_label}</span>
            </li>)}
        </ul>
    </section>
}
