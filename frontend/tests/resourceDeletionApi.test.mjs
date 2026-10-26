import assert from 'node:assert/strict'
import test from 'node:test'
import { canManageResourceDeletion, changeResourceDeletion } from '../src/services/resourceDeletionApi.ts'

test('only an enabled admin can see deletion controls', () => {
    for (const user of [undefined, { role: 'VIEWER', admin_feature_enabled: true },
        { role: 'OPERATOR', admin_feature_enabled: true }, { role: 'ADMIN', admin_feature_enabled: false },
        { role: 'ADMIN', admin_feature_enabled: true, must_change_password: true }]) {
        assert.equal(Boolean(canManageResourceDeletion(user)), false)
    }
    assert.equal(canManageResourceDeletion({ role: 'ADMIN', admin_feature_enabled: true }), true)
})

test('delete and restore use revision fencing, encoded IDs and optional reason', async () => {
    const original = globalThis.fetch
    try {
        for (const restore of [false, true]) {
            globalThis.fetch = async (url, options) => {
                assert.equal(url, `/api/v1/resources/a%2Fb${restore ? '/restore' : ''}`)
                assert.equal(options.method, restore ? 'POST' : 'DELETE')
                assert.deepEqual(JSON.parse(options.body), { expected_revision: 3, reason: null })
                return Response.json({ resource_id: 'a/b', deleted: !restore, revision: 4, changed: true })
            }
            assert.equal((await changeResourceDeletion('a/b', restore, 3, '  ')).deleted, !restore)
        }
    } finally { globalThis.fetch = original }
})

test('conflicts and restore restrictions are actionable and never reported as success', async () => {
    const original = globalThis.fetch
    try {
        for (const [code, text] of [['RESOURCE_DELETION_CONFLICT', /tải lại danh sách/],
            ['RESOURCE_RESTORE_SOURCE_RESTRICTED', /tại nguồn/], ['AUTH_FORBIDDEN', /không có quyền/]]) {
            globalThis.fetch = async () => Response.json({ detail: { code } }, { status: 409 })
            await assert.rejects(changeResourceDeletion('id', true, 1, ''), text)
        }
    } finally { globalThis.fetch = original }
})
