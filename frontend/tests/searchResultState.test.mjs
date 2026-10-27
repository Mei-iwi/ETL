import assert from 'node:assert/strict'
import test from 'node:test'
import { searchEmptyState } from '../src/pages/search/searchResultState.ts'

test('true no-match is distinct from partial index or unavailable model', () => {
    assert.equal(searchEmptyState({ message: 'OK' }).title, 'Chưa tìm thấy học liệu')
    for (const branch_status of [{ metadata: 'METADATA_SCHEMA_NOT_READY', content: 'OK' },
        { content: 'SEARCH_TIMEOUT' }, { content: 'SEMANTIC_MODEL_UNAVAILABLE' }]) {
        assert.equal(searchEmptyState({ message: 'OK', branch_status }).title, 'Tìm kiếm chưa đầy đủ')
    }
    assert.equal(searchEmptyState({ message: 'NO_READY_INDEX' }).title, 'Tìm kiếm chưa đầy đủ')
    assert.equal(searchEmptyState({ message: 'OK', readiness: { reindex_required: true } }).title, 'Tìm kiếm chưa đầy đủ')
})

test('scope denial never claims another user owns matching material', () => {
    for (const message of ['PUBLIC_ACCESS_UNCONFIRMED', 'NO_ACCESSIBLE_CONTENT']) {
        const state = searchEmptyState({ message })
        assert.equal(state.title, 'Chưa có kết quả trong phạm vi truy cập')
        assert.ok(!state.message.includes('riêng tư'))
    }
    assert.equal(searchEmptyState({ message: 'QUERY_NO_KEYWORDS' }).title, 'Cần bổ sung nội dung tìm kiếm')
})
