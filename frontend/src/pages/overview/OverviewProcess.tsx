import style from './OverviewProcess.module.css'
import systemFlow from '../../assets/img/sytemflow.png'

const processSteps = [
    {
        step: 1,
        name: 'Tiếp nhận học liệu',
        content: 'Tải học liệu, nhập thông tin mô tả, tạo phiên bản',
        output: 'Học liệu và Metadata',
    },
    {
        step: 2,
        name: 'Trích xuất nội dung',
        content: 'Bóc tách văn bản, sử dụng OCR hoặc phương pháp xử lý phù hợp với loại dữ liệu được hỗ trợ',
        output: 'Nội dung trích xuất',
    },
    {
        step: 3,
        name: 'Làm sạch và chuẩn hóa',
        content: 'Loại bỏ nhiễu, dữ liệu dư thừa, chuẩn hóa ký tự, gán nhãn và tổ chức nội dung',
        output: 'Nội dung chuẩn hóa',
    },
    {
        step: 4,
        name: 'Phân đoạn dữ liệu',
        content: 'Chia nội dung thành Chunk có thông tin vị trí và nguồn gốc',
        output: 'Danh sách Chunk',
    },
    {
        step: 5,
        name: 'Biểu diễn dữ liệu',
        content: 'Xây dựng biểu diễn Bag of Words và vector ngữ nghĩa',
        output: 'Dữ liệu biểu diễn',
    },
    {
        step: 6,
        name: 'Lập chỉ mục',
        content: 'Tổ chức các biểu diễn và Metadata phục vụ tìm kiếm',
        output: 'Chỉ mục có thể truy vấn',
    },
    {
        step: 7,
        name: 'Tra cứu học liệu',
        content: 'Tiếp nhận truy vấn, lựa chọn BoW, Vector hoặc Hybrid',
        output: 'Kết quả được xếp hạng',
    },
    {
        step: 8,
        name: 'Hiển thị kết quả',
        content: 'Hiển thị đoạn nội dung liên quan, ngữ cảnh và nguồn tham chiếu',
        output: 'Nội dung học liệu phù hợp',
    },
]

export function OverviewProcess() {
    return (
        <section className={style.page}>
            <header className={style.hero}>
                <h1>Quy trình hoạt động</h1>
            </header>

            <section className={style.section}>
                <h2>1. Sơ đồ tổng thể</h2>
                <figure className={style.diagramFigure}>
                    <img
                        className={style.diagram}
                        src={systemFlow}
                        alt="Sơ đồ quy trình xử lý và tra cứu học liệu"
                    />
                    <figcaption className={style.diagramCaption}>
                        Quy trình xử lý và tra cứu dữ liệu của hệ thống
                    </figcaption>
                </figure>
            </section>

            <section className={style.section}>
                <h2>2. Chi tiết các bước xử lý</h2>
                <div className={style.tableWrapper}>
                    <table className={style.processTable}>
                        <thead>
                            <tr>
                                <th scope="col">Bước</th>
                                <th scope="col">Tên bước</th>
                                <th scope="col">Nội dung nên hiển thị</th>
                                <th scope="col">Kết quả đầu ra</th>
                            </tr>
                        </thead>
                        <tbody>
                            {processSteps.map((item) => (
                                <tr key={item.step}>
                                    <td>
                                        <span className={style.stepNumber}>{item.step}</span>
                                    </td>
                                    <td className={style.stepName}>{item.name}</td>
                                    <td>{item.content}</td>
                                    <td>
                                        <span className={style.output}>{item.output}</span>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </section>
        </section>
    )
}
