import { FlaskConical } from 'lucide-react'
import layout from '../library/LibraryPages.module.css'
import styles from './ExperimentPages.module.css'

export function ExperimentPageHeader({ title }: { title: string }) {
    return <header className={`${layout.pageHeader} ${styles.pageHeader}`}>
        <span className={styles.eyebrow}><FlaskConical size={16} aria-hidden="true" /> THỰC NGHIỆM</span>
        <h1>{title}</h1>
    </header>
}
