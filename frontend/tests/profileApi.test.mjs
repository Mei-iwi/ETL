import assert from 'node:assert/strict'
import test from 'node:test'
import { getPersonalProfile, ProfileApiError, updatePersonalProfile } from '../src/services/profileApi.ts'

const fixture = () => ({ full_name: 'Người dùng thử nghiệm', email: 'user@etl.test', role_name: 'Giảng viên',
    access_role: 'VIEWER', is_active: true, email_verified: true, created_at: null, subject_name: null,
    organization_name: 'Trường học', address: null, bio: null, updated_at: null, revision: 'a'.repeat(64),
    editable: true, unavailable_reason: null })

test('own profile uses fixed endpoint, cookie auth and no cache', async () => {
    const old = globalThis.fetch
    try {
        globalThis.fetch = async (url, options) => {
            assert.equal(url, '/api/v1/auth/profile'); assert.equal(options.method, 'GET')
            assert.equal(options.cache, 'no-store'); assert.equal(options.credentials, 'same-origin')
            return Response.json(fixture())
        }
        assert.equal((await getPersonalProfile()).access_role, 'VIEWER')
    } finally { globalThis.fetch = old }
})

test('update sends only supplied editable fields and revision to the fixed endpoint', async () => {
    const old = globalThis.fetch
    const data = { revision: 'a'.repeat(64), organization_name: 'Trường mới', address: null, bio: 'Giới thiệu' }
    try {
        globalThis.fetch = async (url, options) => {
            assert.equal(url, '/api/v1/auth/profile'); assert.equal(options.method, 'PATCH')
            assert.deepEqual(JSON.parse(options.body), data)
            return Response.json({ ...fixture(), ...data })
        }
        assert.equal((await updatePersonalProfile(data)).bio, 'Giới thiệu')
    } finally { globalThis.fetch = old }
})

for (const [status, code] of [[409, 'PROFILE_CHANGED'], [503, 'PROFILE_UNAVAILABLE'], [403, 'PROFILE_NOT_LINKED']]) {
    test('profile errors do not expose backend detail: ' + status, async () => {
        const old = globalThis.fetch
        try {
            globalThis.fetch = async () => Response.json({ detail: { code, message: 'PRIVATE_INTERNAL' } }, { status })
            await assert.rejects(getPersonalProfile(), e => e instanceof ProfileApiError && e.code === code && !e.message.includes('PRIVATE_INTERNAL'))
        } finally { globalThis.fetch = old }
    })
}

test('malformed profile response fails closed', async () => {
    const old = globalThis.fetch
    try {
        globalThis.fetch = async () => Response.json({ ...fixture(), editable: 'true' })
        await assert.rejects(getPersonalProfile(), e => e.code === 'PROFILE_INVALID_RESPONSE')
    } finally { globalThis.fetch = old }
})
