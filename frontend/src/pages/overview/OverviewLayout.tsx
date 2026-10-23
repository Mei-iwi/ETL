import type { ReactNode } from 'react'
import { BookOpen, Library, Search, ArrowUpRight } from 'lucide-react'
import { Link } from 'react-router'
import styles from './OverviewLayout.module.css'

export function OverviewLayout({ title, introduction, children }: {
    title: string; introduction: string; children: ReactNode
}) {
    return <section className={styles.page}>
        <div className={styles.stage}>
            <aside className={styles.card} aria-label="Giới thiệu kho học liệu">
                <div className={styles.cardBody}>
                    <div className={styles.portrait}><BookOpen size={68} strokeWidth={1.2} aria-hidden="true" /></div>
                    <h2>Kho học liệu số</h2>
                    <span className={styles.accent} />
                    <p>Từ tài liệu đến nội dung có thể tìm kiếm</p>
                    <ol className={styles.phases}>
                        <li><span>01</span><div><strong>Chuẩn bị học liệu</strong><p>Tiếp nhận và xử lý nội dung</p></div></li>
                        <li><span>02</span><div><strong>Tra cứu học liệu</strong><p>Tìm kiếm và xem ngữ cảnh</p></div></li>
                    </ol>
                </div>
                <Link to="/library/list" className={styles.cardFooter}><Library size={16} aria-hidden="true" />Khám phá kho học liệu<ArrowUpRight size={14} aria-hidden="true" /></Link>
            </aside>
            <div className={styles.content}>
                <header className={styles.hero}>
                    <h1>{title}</h1><p>{introduction}</p>
                    <div className={styles.actions}>
                        <Link to="/search/query-methods" className={styles.primary}><Search size={15} aria-hidden="true" />Tra cứu học liệu</Link>
                        <Link to="/library/list" className={styles.secondary}><Library size={15} aria-hidden="true" />Kho học liệu</Link>
                    </div>
                </header>
                {children}
            </div>
        </div>
    </section>
}
