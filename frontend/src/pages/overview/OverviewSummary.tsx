import { ArrowUpRight, Library, ScanText, Layers, Search, ChartNoAxesCombined, Activity } from 'lucide-react'
import { Link } from 'react-router'
import { canVisit, useAuth } from '../../auth/AuthProvider'
import { OverviewLayout } from './OverviewLayout'
import style from './OverviewSummary.module.css'
import pha from '../../assets/img/Pha.png'

const features = [
    { title: 'Kho học liệu', text: 'Xem tài liệu, thông tin và các phiên bản được phép truy cập.', path: '/library/list', icon: Library },
    { title: 'Tra cứu nội dung', text: 'Tìm bằng từ khóa, ngữ nghĩa hoặc kết hợp hai phương pháp.', path: '/search/query-methods', icon: Search },
    { title: 'Xử lý học liệu', text: 'Trích xuất, OCR và chuẩn hóa nội dung tài liệu.', path: '/library/processing', icon: ScanText },
    { title: 'Phân đoạn và chỉ mục', text: 'Xem cấu trúc nội dung, ContentBag và trạng thái chỉ mục.', path: '/library/indexing', icon: Layers },
    { title: 'Đánh giá tìm kiếm', text: 'Thực nghiệm và so sánh chất lượng các phương pháp.', path: '/experiments/run', icon: ChartNoAxesCombined },
    { title: 'Trạng thái hệ thống', text: 'Kiểm tra kết nối và khả năng xử lý của hệ thống.', path: '/systems/status', icon: Activity },
]

export function OverviewSummary() {
    const { principal } = useAuth()
    return <OverviewLayout title="Tổng quan hệ thống"
        introduction="Quản lý học liệu, khai thác nội dung và tìm kiếm thông tin trong cùng một không gian.">
        <section className={style.section}>
            <h2>Bạn muốn bắt đầu từ đâu?</h2>
            <div className={style.grid}>
                {features.filter(item => canVisit(item.path, principal)).map(({ title, text, path, icon: Icon }) =>
                    <Link key={path} to={path} className={style.card}>
                        <span className={style.cardIcon}><Icon size={20} strokeWidth={1.6} aria-hidden="true" /></span>
                        <div><h3>{title}</h3><p>{text}</p></div>
                        <ArrowUpRight size={16} className={style.cardArrow} aria-hidden="true" />
                    </Link>)}
            </div>
        </section>
        <section className={style.section}>
            <h2>Học liệu được khai thác như thế nào?</h2>
            <p className={style.content}>Tài liệu được tiếp nhận, trích xuất và tổ chức thành nội dung có thể tra cứu.
                Kết quả tìm kiếm đi kèm ngữ cảnh và nguồn tham chiếu; quyền truy cập được kiểm tra khi xem hoặc tải tệp.</p>
            <div className={style.phaseGrid}>
                <article><span>01</span><h3>Chuẩn bị học liệu</h3><p>Tiếp nhận phiên bản, chuẩn hóa nội dung, tạo ContentBag và chỉ mục.</p></article>
                <article><span>02</span><h3>Tra cứu học liệu</h3><p>Nhập truy vấn, chọn BoW, Vector hoặc Hybrid và xem kết quả phù hợp.</p></article>
            </div>
            <Link className={style.textLink} to="/overview/process">Xem quy trình hoạt động<ArrowUpRight size={15} aria-hidden="true" /></Link>
        </section>
        <section className={style.section}>
            <h2>Mô hình hoạt động hai pha</h2>
            <figure className={style.diagramFigure}>
                <div className={style.diagramViewport} tabIndex={0} role="region" aria-label="Sơ đồ hai pha, có thể cuộn ngang">
                    <img className={style.diagram} src={pha} alt="Pha chuẩn bị dữ liệu và pha tra cứu dữ liệu" />
                </div>
            </figure>
        </section>
    </OverviewLayout>
}
