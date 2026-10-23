import style from './OverviewProcess.module.css'
import { ProcessEntityDiagram } from './ProcessEntityDiagram'
import { OverviewLayout } from './OverviewLayout'

const processSteps = [
    {
        step: 1,
        name: 'Tiếp nhận học liệu',
        content: 'Đồng bộ tham chiếu tệp nguồn hoặc tải tệp LOCAL, nhập thông tin và tạo phiên bản',
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
        name: 'Tổ chức nội dung',
        content: 'Tổ chức ContentUnit theo cấu trúc tài liệu; xem các đoạn nội dung kèm vị trí và nguồn gốc',
        output: 'ContentUnit và bản xem phân đoạn',
    },
    {
        step: 5,
        name: 'Biểu diễn dữ liệu',
        content: 'Tổng hợp từ khóa và tạo vector ngữ nghĩa cho ContentBag',
        output: 'ContentBag với từ khóa và vector',
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
        <OverviewLayout title="Quy trình hoạt động"
            introduction="Theo dõi hành trình từ tài liệu gốc đến kết quả tìm kiếm, qua hai pha chuẩn bị và tra cứu học liệu.">

            <section className={style.section}>
                <h2>Sơ đồ tổng thể</h2>
                <ProcessEntityDiagram />
            </section>

            <section className={style.section}>
                <h2>Các bước xử lý</h2>
                <div className={style.tableWrapper} role="region" aria-label="Chi tiết các bước xử lý" tabIndex={0}>
                    <table className={style.processTable}>
                        <thead>
                            <tr>
                                <th scope="col">Bước</th>
                                <th scope="col">Nội dung xử lý</th>
                                <th scope="col">Kết quả đầu ra</th>
                            </tr>
                        </thead>
                        {[
                            { name: 'Chuẩn bị học liệu', steps: processSteps.slice(0, 6) },
                            { name: 'Truy vấn và hiển thị kết quả', steps: processSteps.slice(6) },
                        ].map(group => <tbody key={group.name}>
                            <tr className={style.groupRow}><th colSpan={3} scope="rowgroup">{group.name}</th></tr>
                            {group.steps.map((item) => (
                                <tr key={item.step}>
                                    <th scope="row"><div className={style.stepCell}><span className={style.stepNumber}>{String(item.step).padStart(2, '0')}</span><span className={style.stepName}>{item.name}</span></div></th>
                                    <td>{item.content}</td>
                                    <td>
                                        <span className={style.output}>{item.output}</span>
                                    </td>
                                </tr>
                            ))}
                        </tbody>)}
                    </table>
                </div>
            </section>
        </OverviewLayout>
    )
}
