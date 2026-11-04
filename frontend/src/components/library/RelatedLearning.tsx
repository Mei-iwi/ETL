import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { useAuth } from '../../auth/AuthProvider'
import { identityScope } from '../../auth/authScope'
import { loadRelatedLearning, type RelatedLearningItem } from '../../services/relatedLearningApi'
import styles from './RelatedLearning.module.css'

export function RelatedLearning({ resourceId }: { resourceId: string }) {
    const { mode, principal } = useAuth()
    const scope = identityScope(mode, principal)
    const [result, setResult] = useState<{ resourceId: string; scope: string; items: RelatedLearningItem[] } | null>(null)
    useEffect(() => {
        const controller = new AbortController()
        void loadRelatedLearning(resourceId, controller.signal).then(items => {
            if (!controller.signal.aborted) setResult({ resourceId, scope, items })
        })
        return () => controller.abort()
    }, [resourceId, scope])
    const items = result?.resourceId === resourceId && result.scope === scope ? result.items : []
    if (!items.length) return null
    return <section className={styles.panel} aria-label="Học liệu liên quan">
        <h2>Học liệu liên quan</h2>
        <ul aria-label="Danh sách học liệu liên quan">
            {items.map(item => <li key={item.resource_id}>
                <Link to={item.url}>{item.title}</Link> — <span>{item.reason_label}</span>
            </li>)}
        </ul>
    </section>
}
