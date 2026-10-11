import style from './OverviewSummary.module.css'
import pha from '../../assets/img/Pha.png'

export function OverviewSummary() {
    return (
        <section className={style.page}>
            <header className={style.hero}>
                <h1>Tổng quan hệ thống</h1>

            </header>

            <section className={style.section}>
                <h2>1. Giới thiệu về hệ thống</h2>
                <p className={style.content}>
                    Hệ thống hướng đến việc tiếp nhận, xử lý, tổ chức và tra cứu học liệu số thuộc nhiều định dạng
                    khác nhau như văn bản, hình ảnh, âm thanh và video. Dữ liệu được chuẩn hóa, phân đoạn và xây dựng
                    các biểu diễn phục vụ tìm kiếm dựa trên từ khóa và ngữ nghĩa.
                </p>
            </section>

            <section className={style.section}>
                <h2>2. Bài toán hệ thống giải quyết</h2>
                <ol className={style.challengeList}>
                    <li><strong>Dữ liệu đa dạng:</strong>
                        <span>Học liệu được lưu ở nhiều định dạng, có thể chứa cả nội dung có cấu trúc và phi cấu trúc.</span>
                    </li>
                    <li><strong>Khó khai thác nội dung chi tiết:</strong>
                        <span>Việc chỉ tìm theo tên tài liệu hoặc metadata chưa đủ để xác định chính xác nội dung người dùng cần.</span>
                    </li>
                    <li><strong>Nhu cầu xử lý và truy vấn thống nhất:</strong>
                        <span>Cần một quy trình chuyển dữ liệu gốc thành nội dung chuẩn hóa, các đoạn dữ liệu và chỉ mục phục vụ tra cứu.</span>
                    </li>
                </ol>
            </section>

            <section className={style.section}>
                <h2>3. Các chức năng chính</h2>
                <div className={style.grid}>
                    <article className={style.card}>
                        <h3>Quản lý học liệu</h3>
                        <p>Tiếp nhận tệp, metadata và quản lý phiên bản.</p>
                    </article>
                    <article className={style.card}>
                        <h3>Xử lý dữ liệu</h3>
                        <p>Trích xuất, làm sạch, chuẩn hóa và tổ chức nội dung.</p>
                    </article>
                    <article className={style.card}>
                        <h3>Biểu diễn dữ liệu</h3>
                        <p>Phân đoạn, Bag of Words, vector hóa và lập chỉ mục.</p>
                    </article>
                    <article className={style.card}>
                        <h3>Tra cứu học liệu</h3>
                        <p>Tìm kiếm BoW, Vector và Hybrid kèm nguồn tham chiếu.</p>
                    </article>
                    <article className={style.card}>
                        <h3>Đánh giá thuật toán</h3>
                        <p>Chạy thực nghiệm, đo độ chính xác và so sánh kết quả.</p>
                    </article>
                    <article className={style.card}>
                        <h3>Theo dõi hệ thống</h3>
                        <p>Kiểm tra trạng thái kết nối và khả năng xử lý.</p>
                    </article>
                </div>
            </section>

            <section className={style.section}>
                <h2>4. Mô hình hoạt động hai pha</h2>
                <figure className={style.diagramFigure}>
                    <div className={style.diagramViewport}>
                        <img
                            className={style.diagram}
                            src={pha}
                            alt="Sơ đồ gồm pha chuẩn bị dữ liệu và pha tra cứu dữ liệu"
                        />
                    </div>
                    <figcaption className={style.diagramCaption}>
                        Quy trình xử lý và tra cứu dữ liệu của hệ thống
                    </figcaption>
                </figure>
            </section>
        </section>
    )
}
