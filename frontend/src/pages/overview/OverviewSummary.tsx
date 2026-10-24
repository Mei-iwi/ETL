import { Link } from 'react-router'
import { canVisit, useAuth } from '../../auth/AuthProvider'
import { OverviewLayout } from './OverviewLayout'
import style from './OverviewSummary.module.css'

const methods = [
    { number: '01', name: 'BoW', label: 'Truy hồi từ vựng', technique: 'BM25', text: 'Đối sánh từ và cụm từ trong nội dung; ưu tiên các thuật ngữ mang thông tin.' },
    { number: '02', name: 'Vector', label: 'Truy hồi ngữ nghĩa', technique: 'BGE-M3', text: 'Tìm nội dung gần nghĩa với truy vấn, kể cả khi cách diễn đạt khác nhau.' },
    { number: '03', name: 'Hybrid', label: 'Kết hợp phương pháp', technique: 'RRF', text: 'Kết hợp kết quả tìm kiếm từ khóa và ngữ nghĩa.' },
]
const tools = [
    { title: 'Kho học liệu', text: 'Học liệu, nguồn tham chiếu và lịch sử phiên bản', path: '/library/list' },
    { title: 'Xử lý và chuẩn hóa', text: 'Trích xuất văn bản, OCR và kiểm tra nội dung', path: '/library/processing' },
    { title: 'Biểu diễn và chỉ mục', text: 'ContentUnit, ContentBag và trạng thái sẵn sàng', path: '/library/indexing' },
]

export function OverviewSummary() {
    const { principal } = useAuth()
    const available = tools.filter(item => canVisit(item.path, principal))
    return <OverviewLayout title={'Quản lý và\ntra cứu học liệu'}
        introduction="Đồng bộ học liệu, xử lý nội dung và tìm kiếm theo từ khóa hoặc ngữ nghĩa.">
        <section className={style.section}>
            <div className={style.sectionHeading}>
                <div><h2>Phương pháp tìm kiếm</h2></div>
                <p>Tìm học liệu bằng từ khóa, ngữ nghĩa hoặc kết hợp cả hai. Kết quả hiển thị theo quyền truy cập.</p>
            </div>
            <div className={style.methods}>{methods.map(method => <article key={method.name}>
                <div className={style.methodTop}><span>{method.number}</span><span>{method.technique}</span></div>
                <h3>{method.name}</h3><h4>{method.label}</h4><p>{method.text}</p>
            </article>)}</div>
        </section>
        <section className={style.experiment}>
            <div><h2>Đánh giá tìm kiếm</h2></div>
            <div><p>So sánh các phương pháp trên tập truy vấn có nhãn liên quan; kiểm tra đoạn nội dung và nguồn tham chiếu.</p>
                <div className={style.metrics}><span>Recall@k</span><span>nDCG@k</span><span>MRR</span></div>
                </div>
        </section>
        <section className={style.section}>
            <div className={style.sectionHeading}><div><h2>Quản lý học liệu</h2></div></div>
            <div className={style.tools}>{available.map(({ title, text, path }, index) => <Link key={path} to={path}>
                <span className={style.toolNumber}>{String(index + 1).padStart(2, '0')}</span><div><h3>{title}</h3><p>{text}</p></div>
            </Link>)}</div>
        </section>
    </OverviewLayout>
}
