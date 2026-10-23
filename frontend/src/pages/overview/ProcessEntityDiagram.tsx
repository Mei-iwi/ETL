import style from './OverviewProcess.module.css'

const nodes = [
    { title: 'Tiếp nhận học liệu và Metadata', x: 150, y: 100, width: 240, color: 'highlight' },
    { title: 'Trích xuất nội dung đa phương tiện', x: 150, y: 200, width: 240, color: 'teal' },
    { title: 'Làm sạch, chuẩn hóa và gán nhãn', x: 150, y: 300, width: 240, color: 'teal' },
    { title: 'Tổ chức nội dung thành ContentUnit', x: 150, y: 400, width: 240, color: 'teal' },
    { title: 'Từ khóa ContentBag', x: 30, y: 540, width: 210, color: 'teal' },
    { title: 'Vector hóa ngữ nghĩa', x: 300, y: 540, width: 210, color: 'teal' },
    { title: 'Tổ chức lưu trữ và lập chỉ mục', x: 150, y: 670, width: 240, color: 'teal' },
    { title: 'Người dùng nhập truy vấn', x: 730, y: 100, width: 240, color: 'highlight' },
    { title: 'Lựa chọn phương pháp tìm kiếm', x: 730, y: 220, width: 240, color: 'blue' },
    { title: 'BoW', x: 570, y: 400, width: 160, color: 'blue' },
    { title: 'Vector', x: 770, y: 400, width: 160, color: 'blue' },
    { title: 'Hybrid', x: 970, y: 400, width: 160, color: 'blue' },
    { title: 'Tìm kiếm trên chỉ mục học liệu', x: 730, y: 670, width: 240, color: 'blue' },
    { title: 'Xếp hạng kết quả', x: 470, y: 830, width: 240, color: 'teal' },
    { title: 'Hiển thị nội dung và nguồn tham chiếu', x: 800, y: 830, width: 300, color: 'result' },
]

export function ProcessEntityDiagram() {
    return <figure className={style.diagramFigure}>
        <div className={style.treeViewport} tabIndex={0} role="region" aria-label="Sơ đồ quy trình, có thể cuộn ngang">
            <svg className={style.treeCanvas} viewBox="0 0 1160 950" role="group" aria-label="Sơ đồ phân nhánh quy trình xử lý và truy vấn học liệu">
                <rect className={style.preparationLane} x="14" y="20" width="512" height="750" rx="14" aria-hidden="true" />
                <rect className={style.searchLane} x="550" y="20" width="596" height="750" rx="14" aria-hidden="true" />
                <foreignObject x="150" y="40" width="300" height="32"><div className={style.laneHeading}><span>01</span> Chuẩn bị học liệu</div></foreignObject>
                <foreignObject x="730" y="40" width="300" height="32"><div className={style.laneHeading}><span>02</span> Truy vấn học liệu</div></foreignObject>
                <g aria-hidden="true">
                    <g fill="none" stroke="#9aabc3" strokeWidth="1.6">
                        <path d="M270 176V200M270 276V300M270 376V400" />
                        <path d="M270 476V510H135V540M270 510H405V540" />
                        <path d="M135 616V645H405V616M270 645V670" />
                        <path d="M850 176V220M850 296V350H650V400M850 350V400M850 350H1050V400" />
                        <path d="M650 476V570H1050V476M850 476V670" />
                        <path d="M390 708H730M850 746V785H590V830M710 868H800" />
                    </g>
                    <g fill="#fff" stroke="#9aabc3" strokeWidth="1.6">
                        <circle cx="270" cy="510" r="5" /><circle cx="270" cy="645" r="5" />
                        <circle cx="850" cy="350" r="5" /><circle cx="850" cy="570" r="5" />
                    </g>
                    <text x="560" y="694" textAnchor="middle" className={style.treeEdgeLabel}>Chỉ mục đã chuẩn bị</text>
                </g>
                {nodes.map(node => <foreignObject key={node.title} x={node.x} y={node.y} width={node.width} height="76" overflow="visible"><div className={`${style.treeNode} ${style[node.color]}`}>{node.title}</div></foreignObject>)}
            </svg>
        </div>
        <figcaption className={style.diagramCaption}>Quy trình xử lý và tra cứu dữ liệu của hệ thống</figcaption>
    </figure>
}
