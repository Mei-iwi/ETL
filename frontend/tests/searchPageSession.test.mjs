import assert from 'node:assert/strict'
import test from 'node:test'
import { clearSearchViewCache, fetchSearchPage, readSearchView } from '../src/pages/search/searchViewCache.ts'
import { searchMaterials, searchSessionPage, searchFailureMessage } from '../src/services/searchApi.ts'
import { searchResponse } from './helpers/searchResponses.mjs'
import { identityScope } from '../src/auth/authScope.ts'

const criteria = { query: 'raw multilingual query', method: 'vector', filters: {}, page: 1, size: 5 }
const token = 's'.repeat(43)
function setup() {
    clearSearchViewCache()
    const calls = { full: 0, page: 0 }
    const clock = { value: 100 }
    const response = request => ({ ...request, items: [{ resource_id: `page-${request.page}`, snippet: 'current evidence' }],
        search_session: { id: token, ttl_seconds: 30 }, total_exact: true })
    const transport = {
        full: async request => { calls.full++; return response(request) },
        page: async request => { calls.page++; return response(request) },
    }
    const run = (page = 1, key = 'verified-alice', changes = {}, signal = new AbortController().signal) =>
        fetchSearchPage(key, { ...criteria, page, ...changes }, signal, transport, () => clock.value)
    return { calls, clock, transport, run }
}
const failure = (status, code) => Object.assign(new Error(code), { status, code })

test('a failure from an older HMR constructor keeps safe actionable text', () => {
    const oldError = Object.assign(new Error('PRIVATE_BACKEND_DETAILS'), { status: 503, code: 'SEARCH_TIMEOUT' })
    assert.match(searchFailureMessage(oldError), /quá thời gian/)
    assert.equal(searchFailureMessage(oldError).includes('PRIVATE_BACKEND_DETAILS'), false)
    assert.match(searchFailureMessage({ status: 403, code: 'PRIVATE_ERROR', message: 'SECRET' }), /hạn chế/)
    assert.equal(searchFailureMessage({ status: 503, code: 'PRIVATE_ERROR', message: 'SECRET' }).includes('SECRET'), false)
})

test('focus refresh of same grants keeps a pending search and token usable', async () => {
    const { transport, calls } = setup()
    const alice = { id: 'alice', role: 'VIEWER', mode: 'master_users', roles: ['viewer'], resource_ids: ['r2', 'r1'] }
    const before = identityScope('master_users', alice)
    let release
    const full = transport.full
    transport.full = request => new Promise(resolve => { release = () => full(request).then(resolve) })
    const pending = fetchSearchPage(before, criteria, new AbortController().signal, transport)
    const after = identityScope('master_users', { ...alice, resource_ids: ['r1', 'r2'], display_name: 'new display name' })
    if (before !== after) clearSearchViewCache()
    release()
    assert.ok((await pending).search_session)
    await fetchSearchPage(after, { ...criteria, page: 2 }, new AbortController().signal, transport)
    assert.deepEqual(calls, { full: 1, page: 1 })
})

test('actor/role/permission/admin flag changes invalidate UI identity', () => {
    const alice = { id: 'alice', mode: 'master_users', role: 'VIEWER', roles: ['viewer'],
        resource_ids: ['r1'], permissions: [], admin_feature_enabled: false }
    const original = identityScope('master_users', alice)
    for (const change of [{ id: 'bob' }, { role: 'ADMIN' }, { roles: ['operator'] }, { permissions: ['process'] },
        { resource_ids: [] }, { must_change_password: true }, { admin_feature_enabled: true }])
        assert.notEqual(identityScope('master_users', { ...alice, ...change }), original)
    assert.notEqual(identityScope('db_users', alice), original)
})

test('rank session reused for pages 2/3/1/4; each page freshly hydrated', async () => {
    const { run, calls } = setup()
    for (const page of [1, 2, 3, 1, 4]) assert.equal((await run(page)).items[0].resource_id, `page-${page}`)
    assert.deepEqual(calls, { full: 1, page: 4 })
    assert.equal(readSearchView('verified-alice'), null)
})
test('typed stale session falls back once', async () => {
    const { run, calls, transport } = setup()
    await run()
    transport.page = async () => { calls.page++; throw failure(409, 'SEARCH_SESSION_MISS') }
    await run(2)
    assert.deepEqual(calls, { full: 2, page: 1 })
})
for (const status of [401, 403, 503]) test(`HTTP ${status} never downgraded to heavy retry`, async () => {
    const { run, calls, transport } = setup()
    await run()
    transport.page = async () => { throw failure(status, 'SEARCH_UNAVAILABLE') }
    await assert.rejects(run(2), error => error.status === status)
    assert.equal(calls.full, 1)
})
test('actor/query/method/filter/size changes cannot share a session', async () => {
    const { run, calls } = setup()
    await run()
    await run(1, 'verified-bob')
    for (const changes of [{ query: 'new' }, { method: 'bow' }, { filters: { subject_id: 'math' } }, { size: 10 }])
        await run(1, 'verified-alice', changes)
    assert.deepEqual(calls, { full: 6, page: 0 })
})
test('expired/logout/manual refresh discards session', async () => {
    const { run, calls, clock } = setup()
    await run(); clock.value += 31_000; await run(2)
    clearSearchViewCache(); await run(3)
    assert.equal(calls.full, 3); assert.equal(calls.page, 0)
})
test('clear during in-flight request rejects stale response', async () => {
    const { run, transport } = setup()
    let release
    transport.full = () => new Promise(resolve => { release = resolve })
    const pending = run()
    clearSearchViewCache()
    release({ search_session: { id: token, ttl_seconds: 30 }, total_exact: true })
    await assert.rejects(pending, error => error.name === 'AbortError')
})
test('aborted StrictMode probe never starts transport', async () => {
    const { run, calls } = setup()
    const controller = new AbortController(); controller.abort()
    await assert.rejects(run(1, 'alice', {}, controller.signal), error => error.name === 'AbortError')
    assert.deepEqual(calls, { full: 0, page: 0 })
})
test('legacy or invalid optional session fields retain full-search behavior', async () => {
    const { run, transport, calls } = setup()
    transport.full = async request => {
        assert.equal('window_pages' in request, false) // Old strict servers accept it.
        calls.full++; return { ...request, items: [], search_session: { id: 'bad', ttl_seconds: Infinity } }
    }
    await run(); await run(2)
    assert.deepEqual(calls, { full: 2, page: 0 })
})
test('bounded LRU evicts oldest criteria', async () => {
    const { run, calls } = setup()
    for (let i = 0; i < 9; i++) await run(1, `actor-${i}`)
    await run(2, 'actor-0')
    assert.equal(calls.full, 10)
})

test('real frontend transport validates full/page wire contract with one heavy request', async () => {
    clearSearchViewCache()
    const old = globalThis.fetch
    const paths = []
    try {
        globalThis.fetch = async (path, init) => {
            const body = JSON.parse(init.body)
            paths.push(path)
            assert.equal(init.cache, 'no-store')
            if (path.endsWith('/page')) assert.equal(body.session_id, token)
            else assert.equal('window_pages' in body, false)
            const response = searchResponse(body)
            response.total = 22; response.total_pages = 5
            response.items[0].resource_id = `r${body.page}`
            response.search_session = { id: token, ttl_seconds: 30 }
            response.total_exact = true
            return Response.json(response)
        }
        for (const page of [1, 2, 3, 1, 4]) {
            const result = await fetchSearchPage('verified-alice', { ...criteria, page }, new AbortController().signal,
                { full: searchMaterials, page: searchSessionPage })
            assert.equal(result.items[0].resource_id, `r${page}`)
        }
        assert.deepEqual(paths, ['/api/v1/search', ...Array(4).fill('/api/v1/search/page')])
    } finally { globalThis.fetch = old; clearSearchViewCache() }
})
