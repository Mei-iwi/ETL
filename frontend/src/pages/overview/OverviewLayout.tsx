import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { canVisit, useAuth } from '../../auth/AuthProvider'
import styles from './OverviewLayout.module.css'
import libraryPhoto from '../../assets/img/library-room.jpg'

// Local photo-1507842217343-583bb7270b66. License: https://unsplash.com/license.
export function OverviewLayout({ title, introduction, chapter = '01', children }: {
    title: string; introduction: string; chapter?: '01' | '02'; children: ReactNode
}) {
    const { principal } = useAuth()
    const research = canVisit('/experiments/run', principal)
    return <section className={styles.page}>
        <header className={styles.hero}>
            <div className={styles.heroCopy}>
                <p className={styles.eyebrow}>HỆ THỐNG HỌC LIỆU SỐ</p>
                <h1>{title}</h1><p className={styles.introduction}>{introduction}</p>
                <div className={styles.actions}>
                    <Link to={research ? '/experiments/run' : '/search/query-methods'} className={styles.primary}>
                        {research ? 'Thực nghiệm tìm kiếm' : 'Tra cứu học liệu'}</Link>
                    <Link to={chapter === '01' ? '/overview/process' : '/overview/summary'} className={styles.secondary}>
                        {chapter === '01' ? 'Quy trình hoạt động' : 'Giới thiệu hệ thống'}</Link>
                </div>
            </div>
            <figure className={styles.cover}>
                <div className={styles.mainPhoto}>
                    <img src={libraryPhoto} alt="Kệ sách trong thư viện" width="1200" height="704" />
                </div>
            </figure>
        </header>
        <div id="overview-content" className={styles.content}>{children}</div>
    </section>
}
