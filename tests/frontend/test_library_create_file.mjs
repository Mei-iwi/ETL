import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from '../../frontend/node_modules/typescript/lib/typescript.js'

// Exercise the real component handlers across renders without a browser or API.
// The ref lifecycle below reproduces the file input unmounting after selection.
async function page() {
    const slots = []
    const effects = []
    const requests = []
    const navigations = []
    const savedPreferences = []
    let cursor = 0
    let previousRefs = []
    const react = {
        useState(initial) {
            const index = cursor++
            if (!(index in slots)) slots[index] = initial
            return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value }]
        },
        useRef(initial) {
            const index = cursor++
            if (!(index in slots)) slots[index] = { current: initial }
            return slots[index]
        },
        useEffect(callback) {
            const index = cursor++
            if (!(index in slots)) { slots[index] = true; effects.push(callback) }
        },
    }
    const preferencesModule = { exports: {} }
    vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL('../../frontend/src/services/processingPreferences.ts', import.meta.url), 'utf8'), {
        compilerOptions: { module: ts.ModuleKind.CommonJS },
    }).outputText, preferencesModule)
    const modules = {
        react,
        'react/jsx-runtime': { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) },
        'react-router': { useNavigate: () => (...args) => navigations.push(args) },
        '../../services/libraryLookupApi': {
            getResourceTypes: async () => [{ code: 'HANDOUT', name: 'Handout' }],
            getProviders: async () => [{ id: 'provider-1', name: 'Provider', provider_type: 'SCHOOL' }],
            getGradeLevels: async () => [], getSubjects: async () => [],
        },
        '../../services/processingApi': { startProcessing: async () => ({ job_id: 'job-1' }) },
        '../../services/processingPreferences': {
            ...preferencesModule.exports,
            saveProcessingPreferences: async (_, value) => { savedPreferences.push(value) },
        },
        '../../components/ProcessingPreferences': { ProcessingPreferences: () => null },
        './LibraryCreatePages.module.css': {},
    }
    class FormDataFromForm extends FormData {
        constructor(form) {
            super()
            for (const [key, value] of Object.entries(form.fields)) this.set(key, value)
        }
    }
    const source = fs.readFileSync(new URL('../../frontend/src/pages/library/LibraryCreatePage.tsx', import.meta.url), 'utf8')
    const output = ts.transpileModule(source, { compilerOptions: {
        module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText
    const context = {
        exports: {}, require(name) { assert.ok(name in modules, name); return modules[name] },
        AbortController, File, FormData: FormDataFromForm,
        fetch: async (url, options) => {
            requests.push({ url, options })
            return { ok: true, json: async () => ({ initial_version_id: 'version-1' }) }
        },
    }
    // No DataTransfer global: choosing a file must not depend on its constructor.
    vm.runInNewContext(output, context)
    let tree
    function nodes(value) {
        if (Array.isArray(value)) return value.flatMap(node => nodes(node))
        if (!value || typeof value !== 'object') return []
        return [value, ...nodes(value.props?.children)]
    }
    function render() {
        cursor = 0
        tree = context.exports.LibraryCreatePage()
        const refs = nodes(tree).filter(node => node.props?.ref).map(node => node.props.ref)
        for (const ref of previousRefs) if (!refs.includes(ref)) ref.current = null
        for (const ref of refs) ref.current ??= { files: [], value: '' }
        previousRefs = refs
    }
    function find(predicate) { const node = nodes(tree).find(predicate); assert.ok(node, 'Expected rendered control'); return node }
    render()
    for (const effect of effects.splice(0)) effect()
    await new Promise(resolve => setImmediate(resolve))
    render()
    find(node => node.props?.name === 'resource_type_code').props.onChange({ target: { value: 'HANDOUT' } })
    find(node => node.props?.name === 'provider_id').props.onChange({ target: { value: 'provider-1' } })
    render()
    return {
        requests, navigations, savedPreferences,
        pick(file) { find(node => node.props?.type === 'file').props.onChange({ target: { files: [file] } }); render() },
        drop(file) { find(node => typeof node.props?.onDrop === 'function').props.onDrop({ preventDefault() {}, dataTransfer: { files: [file] } }); render() },
        remove() { find(node => node.props?.['aria-label'] === 'Bỏ tệp đã chọn').props.onClick(); render() },
        hasPicker() { return nodes(tree).some(node => node.props?.type === 'file') },
        preferences() { return nodes(tree).find(node => node.type === modules['../../components/ProcessingPreferences'].ProcessingPreferences) },
        setSource(language) {
            const control = this.preferences()
            assert.ok(control)
            control.props.onChange({ ...control.props.value, source_language: language })
            render()
        },
        async submit() {
            await find(node => node.type === 'form').props.onSubmit({
                preventDefault() {}, currentTarget: { fields: { title: 'Test resource', resource_type_code: 'HANDOUT', provider_id: 'provider-1' } },
            })
            render()
        },
    }
}

for (const selection of ['pick', 'drop']) {
    test(`${selection}: upload retains the selected file after the picker unmounts`, async () => {
        const form = await page()
        const file = new File(['selected file bytes'], 'bài học.mp3', { type: 'audio/mpeg' })
        form[selection](file)
        assert.equal(form.hasPicker(), false)
        await form.submit()
        assert.equal(form.requests.length, 1)
        const request = form.requests[0]
        assert.equal(request.url, '/api/v1/resources')
        const uploaded = request.options.body.get('file')
        assert.equal(uploaded.name, file.name)
        assert.equal(await uploaded.text(), 'selected file bytes')
        assert.equal(request.options.body.get('title'), 'Test resource')
        assert.equal(form.navigations.length, 1)
    })
}

test('removing the file blocks submission and selecting a replacement uploads only the new file', async () => {
    const form = await page()
    form.pick(new File(['old'], 'old.pdf', { type: 'application/pdf' }))
    form.remove()
    assert.equal(form.hasPicker(), true)
    await form.submit()
    assert.equal(form.requests.length, 0)
    form.pick(new File(['replacement'], 'replacement.mp4', { type: 'video/mp4' }))
    await form.submit()
    assert.equal(form.requests.length, 1)
    const files = form.requests[0].options.body.getAll('file')
    assert.equal(files.length, 1)
    assert.equal(files[0].name, 'replacement.mp4')
    assert.equal(await files[0].text(), 'replacement')
})

test('submitting without selecting a file does not call the upload API', async () => {
    const form = await page()
    await form.submit()
    assert.equal(form.requests.length, 0)
})

test('media options are hidden before selection and for documents, and shown for MP3/MP4 only', async () => {
    const form = await page()
    assert.equal(form.preferences(), undefined)
    form.pick(new File(['pdf'], 'lesson.pdf'))
    assert.equal(form.preferences(), undefined)
    form.remove()
    form.pick(new File(['audio'], 'lesson.MP3'))
    assert.equal(form.preferences().props.mediaType, 'audio')
    assert.equal(form.preferences().props.variant, 'card')
    form.remove()
    assert.equal(form.preferences(), undefined)
    form.pick(new File(['video'], 'lesson.MP4'))
    assert.equal(form.preferences().props.mediaType, 'video')
})

test('switching from media to a document does not submit hidden media settings', async () => {
    const form = await page()
    form.pick(new File(['audio'], 'lesson.mp3'))
    form.setSource('fr')
    form.remove()
    form.pick(new File(['pdf'], 'lesson.pdf'))
    await form.submit()
    assert.equal(form.savedPreferences.length, 1)
    assert.equal(form.savedPreferences[0].source_language, null)
})
