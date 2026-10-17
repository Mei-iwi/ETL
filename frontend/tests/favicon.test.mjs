import assert from 'node:assert/strict'
import test from 'node:test'
import { spawn, spawnSync } from 'node:child_process'
import { mkdir, mkdtemp, readFile } from 'node:fs/promises'
import { createServer } from 'node:net'
import { resolve } from 'node:path'

const vite = resolve('frontend/node_modules/vite/bin/vite.js')
async function freePort() {
    const server = createServer()
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const port = server.address().port
    await new Promise(resolve => server.close(resolve))
    return port
}
async function verify(mode, args, base) {
    const port = await freePort()
    const child = spawn(process.execPath, [vite, ...args, '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
        { cwd: resolve('frontend'), windowsHide: true, stdio: 'ignore' })
    try {
        const origin = 'http://127.0.0.1:' + port
        let response
        for (let i = 0; i < 100; i++) {
            response = await fetch(origin + base).catch(() => null)
            if (response?.ok) break
            await new Promise(resolve => setTimeout(resolve, 100))
        }
        assert.equal(response?.status, 200, mode)
        const html = await response.text()
        const icons = [...html.matchAll(/<link[^>]+rel="icon"[^>]*>/g)]
        assert.equal(icons.length, 1)
        assert.ok(icons[0][0].includes('type="image/png"'))
        const href = /href="([^"]+)"/.exec(icons[0][0])[1]
        assert.equal(href, base + 'favicon.png')
        const icon = await fetch(new URL(href, origin))
        assert.equal(icon.status, 200)
        assert.ok(icon.headers.get('content-type').startsWith('image/png'))
        assert.deepEqual(Buffer.from(await icon.arrayBuffer()).subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
    } finally {
        const exited = new Promise(resolve => child.once('exit', resolve))
        child.kill()
        await exited
    }
}

test('favicon public asset is PNG64 and HTML has one base-aware icon', async () => {
    const data = await readFile('frontend/public/favicon.png')
    assert.equal(data.readUInt32BE(16), 64)
    assert.equal(data.readUInt32BE(20), 64)
    const html = await readFile('frontend/index.html', 'utf8')
    // Vite rewrites a public root asset for BASE_URL in both dev and build.
    assert.ok(html.includes('href="/favicon.png"'))
})
test('favicon dev HTTP200 and correct MIME below subpath', { timeout: 30000 }, () =>
    verify('dev', ['--base', '/etl-test/'], '/etl-test/'))
test('favicon build/preview HTTP200 below subpath', { timeout: 60000 }, async () => {
    await mkdir('.test-tmp', { recursive: true })
    const output = await mkdtemp(resolve('.test-tmp/favicon-'))
    const built = spawnSync(process.execPath, [vite, 'build', '--base', '/etl-test/', '--outDir', output],
        { cwd: resolve('frontend'), windowsHide: true, encoding: 'utf8' })
    assert.equal(built.status, 0, built.stderr)
    await verify('preview', ['preview', '--base', '/etl-test/', '--outDir', output], '/etl-test/')
})
