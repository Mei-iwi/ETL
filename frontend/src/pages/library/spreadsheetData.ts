export type GridCell = { text: string; bold?: boolean; italic?: boolean; color?: string; background?: string; align?: 'left' | 'center' | 'right' }
export type SpreadsheetData = { kind: 'spreadsheet'; sheets: { name: string; rows: GridCell[][] }[]; truncated: boolean }

export function parseSpreadsheetPreview(value: unknown): SpreadsheetData {
    if (!value || typeof value !== 'object' || !('kind' in value) || value.kind !== 'spreadsheet'
        || !('sheets' in value) || !Array.isArray(value.sheets) || value.sheets.length > 20) throw new Error('Bản xem bảng tính không hợp lệ.')
    let cells = 0
    const sheets = value.sheets.map(sheet => {
        if (!sheet || typeof sheet.name !== 'string' || !Array.isArray(sheet.rows) || sheet.rows.length > 500) throw new Error('Bản xem bảng tính không hợp lệ.')
        const rows = sheet.rows.map((row: unknown) => {
            if (!Array.isArray(row) || row.length > 100 || (cells += row.length) > 50000) throw new Error('Bảng tính vượt giới hạn xem trước.')
            return row.map((cell: unknown): GridCell => {
                if (!cell || typeof cell !== 'object' || !('text' in cell) || typeof cell.text !== 'string' || cell.text.length > 2000) throw new Error('Ô bảng tính không hợp lệ.')
                const item = cell as GridCell
                return { text: item.text, bold: item.bold === true, italic: item.italic === true,
                    color: typeof item.color === 'string' && /^#[0-9a-f]{6}$/i.test(item.color) ? item.color : undefined,
                    background: typeof item.background === 'string' && /^#[0-9a-f]{6}$/i.test(item.background) ? item.background : undefined,
                    align: ['left', 'center', 'right'].includes(item.align || '') ? item.align : undefined }
            })
        })
        return { name: sheet.name.slice(0, 128), rows }
    })
    return { kind: 'spreadsheet', sheets, truncated: 'truncated' in value && value.truncated === true }
}

export function columnLabel(index: number): string {
    let label = ''
    for (let value = index + 1; value > 0; value = Math.floor((value - 1) / 26)) label = String.fromCharCode(65 + (value - 1) % 26) + label
    return label
}
