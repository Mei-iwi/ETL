import assert from 'node:assert/strict'
import test from 'node:test'
import { metadataReadOnlyMessage, updateResourceMetadata } from '../src/services/resourceApi.ts'

const payload = { title: ' Local title ', resource_type_code: 'DOC', resource_type_name_vi: 'Tài liệu',
    provider_id: 'p1', provider_name: 'Provider', provider_type: 'INTERNAL',
    subject_id: null, grade_level_id: null, publication_status: 'PUBLISHED' }

test('metadata editing is LOCAL only, SOURCE and unverified origins explain read-only', () => {
    assert.equal(metadataReadOnlyMessage('LOCAL'), null)
    assert.match(metadataReadOnlyMessage('POSTGRESQL'), /SOURCE.*nguồn.*đồng bộ/)
    for (const origin of [undefined, null, 'UNKNOWN', 'unsupported', 'local']) {
        assert.match(metadataReadOnlyMessage(origin), /Chưa xác minh/)
    }
})

test('LOCAL PATCH preserves route, trimming and request payload', async () => {
    const original = globalThis.fetch
    try {
        globalThis.fetch = async (url, options) => {
            assert.equal(url, '/api/v1/resources/local-1/metadata')
            assert.equal(options.method, 'PATCH')
            const body = JSON.parse(options.body)
            assert.equal(body.title, 'Local title')
            assert.equal(body.publication_status, 'PUBLISHED')
            assert.equal(body.subject_id, null)
            return Response.json({ id: 'local-1', ...body })
        }
        assert.equal((await updateResourceMetadata('local-1', payload)).id, 'local-1')
    } finally { globalThis.fetch = original }
})

test('ownership conflict is shown as Vietnamese business error, not persistence outage', async () => {
    const original = globalThis.fetch
    try {
        globalThis.fetch = async () => Response.json({ detail: { code: 'RESOURCE_SOURCE_OWNED_READ_ONLY',
            message: 'Học liệu được quản lý từ SOURCE. Vui lòng sửa tại nguồn và đồng bộ lại.' } }, { status: 409 })
        await assert.rejects(updateResourceMetadata('source-1', payload), /SOURCE.*nguồn.*đồng bộ/)
    } finally { globalThis.fetch = original }
})
