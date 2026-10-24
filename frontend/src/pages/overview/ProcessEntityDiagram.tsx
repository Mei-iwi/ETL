import style from './OverviewProcess.module.css'

const lanes = [
    { title: '01 · Chuẩn bị học liệu', steps: ['Tiếp nhận tài liệu', 'Trích xuất / OCR', 'Chuẩn hóa nội dung', 'ContentUnit', 'ContentBag', 'Lập chỉ mục'] },
    { title: '02 · Truy hồi và đánh giá', steps: ['Nhập truy vấn', 'BoW / Vector / Hybrid', 'Xếp hạng kết quả', 'Nội dung và ngữ cảnh', 'Thực nghiệm / đánh giá'] },
]

export function ProcessEntityDiagram() {
    return <div className={style.flow}>
        {lanes.map(lane => <section key={lane.title}>
            <h3>{lane.title}</h3>
            <div className={style.flowScroll} role="region" aria-label={lane.title} tabIndex={0}>
                <ol className={style.flowSteps}>{lane.steps.map((step, index) =>
                    <li key={step}><span>{index + 1}</span><strong>{step}</strong></li>)}</ol>
            </div>
        </section>)}
    </div>
}
