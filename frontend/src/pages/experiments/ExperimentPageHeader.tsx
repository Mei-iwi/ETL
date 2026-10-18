import layout from '../library/LibraryPages.module.css'
import styles from './ExperimentPages.module.css'

export function ExperimentPageHeader({ title }: { title: string }) {
    return <header className={`${layout.pageHeader} ${styles.pageHeader}`}>
        <h1>{title}</h1>
    </header>
}
