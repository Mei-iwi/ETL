import assert from 'node:assert/strict'
import test from 'node:test'
import { getSearchModelStatus, retrySearchModel } from '../src/services/searchApi.ts'

test('readiness validates state and ready; never accepts false READY or private errors', async () => {
    const original = globalThis.fetch
    try {
        for (const state of ['LOADING', 'READY', 'FAILED', 'STOPPED', 'UNINITIALIZED']) {
            globalThis.fetch = async (url, init) => {
                assert.equal(url, '/api/v1/search/model-status')
                assert.equal(init.method, 'GET')
                return Response.json({ state, ready: state === 'READY' })
            }
            assert.equal((await getSearchModelStatus()).state, state)
        }
        for (const value of [null, [], {}, { state: 'READY', ready: false }, { state: 'LOADING', ready: true }]) {
            globalThis.fetch = async () => Response.json(value)
            await assert.rejects(getSearchModelStatus(), error => error.code === 'SEARCH_INVALID_RESPONSE')
        }
        globalThis.fetch = async () => Response.json({ detail: { code: 'PRIVATE_INTERNAL' } }, { status: 503 })
        await assert.rejects(getSearchModelStatus(), error => !error.message.includes('PRIVATE_INTERNAL'))
    } finally { globalThis.fetch = original }
})

test('manual model retry is POST, cancellation propagates and forbidden is sanitized', async () => {
    const original = globalThis.fetch
    try {
        globalThis.fetch = async (url, init) => {
            assert.equal(url, '/api/v1/search/model-retry')
            assert.equal(init.method, 'POST')
            assert.equal(init.body, undefined)
            return Response.json({ state: 'LOADING', ready: false })
        }
        assert.equal((await retrySearchModel()).state, 'LOADING')
        globalThis.fetch = async () => Response.json({ detail: 'PRIVATE' }, { status: 403 })
        await assert.rejects(retrySearchModel(), error => error.status === 403 && !error.message.includes('PRIVATE'))
        const controller = new AbortController()
        controller.abort()
        await assert.rejects(retrySearchModel(controller.signal), error => error.name === 'AbortError')
    } finally { globalThis.fetch = original }
})
