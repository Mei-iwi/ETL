import { useState } from 'react'
import { columnLabel } from './spreadsheetData'
import type { SpreadsheetData } from './spreadsheetData'
import styles from './LibraryResourceDetailPage.module.css'

export function SpreadsheetPreview({ data }: { data: SpreadsheetData }) {
    const [selected, setSelected] = useState(0)
    const [page, setPage] = useState(0)
    const sheet = data.sheets[selected]
    const columns = Math.max(1, ...(sheet?.rows.map(row => row.length) || []))
    const offset = page * 100
    return <div className={styles.spreadsheet}>
        <div role="tablist" aria-label="Sheet bảng tính" className={styles.sheetTabs}>
            {data.sheets.map((item, index) => <button key={index} role="tab" aria-selected={selected === index}
                onClick={() => { setSelected(index); setPage(0) }}>{item.name}</button>)}
        </div>
        <div className={styles.gridScroll} role="region" aria-label={`Bảng tính ${sheet?.name || ''}`} tabIndex={0}>
            <table className={styles.excelGrid} aria-label={sheet?.name || 'Bảng tính'}>
                <thead><tr><th aria-label="Số hàng" />{Array.from({ length: columns }, (_, i) => <th scope="col" key={i}>{columnLabel(i)}</th>)}</tr></thead>
                <tbody>{sheet?.rows.slice(offset, offset + 100).map((row, i) => <tr key={offset + i}>
                    <th scope="row">{offset + i + 1}</th>{Array.from({ length: columns }, (_, column) => {
                        const cell = row[column]
                        return <td key={column} style={cell ? { fontWeight: cell.bold ? 'bold' : undefined,
                            fontStyle: cell.italic ? 'italic' : undefined, color: cell.color, backgroundColor: cell.background,
                            textAlign: cell.align } : undefined}>{cell?.text || ''}</td>
                    })}
                </tr>)}</tbody>
            </table>
        </div>
        <div className={styles.sheetTabs}>
            <button disabled={page === 0} onClick={() => setPage(value => value - 1)}>Hàng trước</button>
            <span>{sheet?.rows.length ? `${offset + 1}–${Math.min(offset + 100, sheet.rows.length)}` : '0'} / {sheet?.rows.length || 0} hàng</span>
            <button disabled={offset + 100 >= (sheet?.rows.length || 0)} onClick={() => setPage(value => value + 1)}>Hàng tiếp</button>
        </div>
        <p className={styles.message}>Bản xem chỉ đọc, không tính lại công thức. Tải tệp để mở đầy đủ trong Excel.</p>
        {data.truncated && <p role="status" className={styles.message}>Đang hiển thị một phần bảng tính (tối đa 500 hàng, 100 cột mỗi sheet). Tải tệp để xem toàn bộ.</p>}
    </div>
}
