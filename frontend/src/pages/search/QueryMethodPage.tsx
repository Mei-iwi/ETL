import { Link, useSearchParams } from 'react-router'
import layout from '../library/LibraryPages.module.css'
import styles from './SearchPages.module.css'
import { methods } from './searchParams'

export function QueryMethodPage() {
    const [params] = useSearchParams()
    return <section className={layout.page}>
        <header className={layout.pageHeader}><h1>Phương pháp truy vấn</h1></header>
        <div className={styles.stack}>{methods.map(method => {
            const target = new URLSearchParams(params)
            target.set('method', method.value)
            return <article className={layout.card} key={method.value}>
                <h2>{method.label}</h2><p>{method.description}</p>
                <Link className={layout.primaryButton} to={`/search/materials?${target}`}>Chọn {method.label}</Link>
            </article>
        })}</div>
    </section>
}
