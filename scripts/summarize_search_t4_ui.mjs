// Compare isolated UI fixture runs; never connects to an app or database.
import assert from 'node:assert/strict'
import { readFile, writeFile } from 'node:fs/promises'

const [baselinePath, candidatePath, outputPath] = process.argv.slice(2)
assert.ok(baselinePath && candidatePath && outputPath, 'Usage: node scripts/summarize_search_t4_ui.mjs BASELINE CANDIDATE OUTPUT')
const baseline = JSON.parse(await readFile(baselinePath, 'utf8'))
const candidate = JSON.parse(await readFile(candidatePath, 'utf8'))
assert.equal(baseline.baseline, true)
assert.equal(candidate.baseline, false)
assert.equal(baseline.samples.length, 300)
assert.equal(candidate.samples.length, 300)
for (let i = 0; i < 300; i++) {
    const a = baseline.samples[i], b = candidate.samples[i]
    assert.equal(a.temperature, b.temperature)
    assert.equal(a.group, b.group)
    assert.equal(a.response.status, 200)
    assert.equal(b.response.status, 200)
    assert.equal(a.response.sha256, b.response.sha256, `Wire parity failed at sample ${i}`)
    assert.equal(a.posts, 1)
    assert.equal(b.posts, 1)
    assert.equal(a.cache, 'OFF')
    assert.equal(b.cache, 'OFF')
}
const distribution = values => {
    const sorted = [...values].sort((a, b) => a - b)
    return { p50: sorted[Math.ceil(sorted.length * .5) - 1], p95: sorted[Math.ceil(sorted.length * .95) - 1] }
}
const groups = []
for (const temperature of ['cold', 'warm']) for (const group of ['first', 'new-2', 'new-3', 'visited', 'back']) {
    const row = { temperature, group }
    for (const [name, run] of Object.entries({ baseline, candidate })) {
        const samples = run.samples.filter(sample => sample.temperature === temperature && sample.group === group)
        assert.equal(samples.length, 30)
        const catalogs = samples.reduce((total, sample) => total + sample.catalogs, 0)
        assert.equal(catalogs, name === 'candidate' && group !== 'first' ? 0 : 90)
        row[name] = { count: 30, latency_ms: distribution(samples.map(sample => sample.wall_ms)),
            harness_cpu_ms: distribution(samples.map(sample => sample.harness_cpu_ms)),
            harness_rss_bytes: distribution(samples.map(sample => sample.harness_rss_bytes)),
            catalog_requests: catalogs, search_posts: 30, statuses: { 200: 30, 409: 0, 503: 0 } }
    }
    groups.push(row)
}
const report = { status: 'UI_FALLBACK_PASS', exact_wire_parity_pairs: 300, groups,
    scope: 'UI fixture only. Cold=form remount; warm=existing Search. CPU/RSS=Node fixture/CDP harness. No SQL/Mongo/model exists in this fixture; their metrics are null in raw samples. Not a cache benchmark.',
    cache: { frontend_validated: 'OFF', backend_ranked: 'OFF', embedding: 'OFF', hits: 0, misses: null, invalidations: null },
    backend_benchmark: 'NOT_RUN: backend source identical to T3; independent frontend fallback only' }
await writeFile(outputPath, JSON.stringify(report, null, 2) + '\n')
console.log(JSON.stringify({ status: report.status, exact_wire_parity_pairs: report.exact_wire_parity_pairs,
    groups: groups.map(row => ({ temperature: row.temperature, group: row.group,
        baseline: row.baseline.latency_ms, candidate: row.candidate.latency_ms })) }, null, 2))
