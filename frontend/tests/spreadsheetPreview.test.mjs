import assert from 'node:assert/strict'
import test from 'node:test'
import { columnLabel, parseSpreadsheetPreview } from '../src/pages/library/spreadsheetData.ts'

test('Excel column headers include double-letter columns', () => {
    assert.deepEqual([0, 25, 26, 51, 52, 99].map(columnLabel), ['A', 'Z', 'AA', 'AZ', 'BA', 'CV'])
})

test('grid keeps literal text/formulas and allows only safe cell styles', () => {
    const data = parseSpreadsheetPreview({ kind: 'spreadsheet', sheets: [{ name: 'Sheet 1', rows: [[
        { text: '<script>alert(1)</script>', color: 'url(https://private)', background: '#123456', align: 'right', bold: true },
        { text: '=HYPERLINK("https://private")' },
    ]] }], truncated: true })
    assert.equal(data.sheets[0].rows[0][0].text, '<script>alert(1)</script>')
    assert.equal(data.sheets[0].rows[0][0].color, undefined)
    assert.equal(data.sheets[0].rows[0][0].background, '#123456')
    assert.equal(data.sheets[0].rows[0][1].text, '=HYPERLINK("https://private")')
    assert.equal(data.truncated, true)
})

test('malformed and oversized grids fail closed', () => {
    for (const data of [null, {}, { kind: 'spreadsheet', sheets: [null] },
        { kind: 'spreadsheet', sheets: [{ name: 'S', rows: [Array(101).fill({ text: '' })] }] },
        { kind: 'spreadsheet', sheets: [{ name: 'S', rows: [[{ text: 2 }]] }] }]) {
        assert.throws(() => parseSpreadsheetPreview(data))
    }
})
