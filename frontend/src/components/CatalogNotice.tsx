import { catalogMessage, type CatalogItems } from '../services/libraryLookupApi'

export function CatalogNotice({ providers, types }: { providers: CatalogItems<unknown>; types: CatalogItems<unknown> }) {
    const notices = [
        { label: 'Nhà cung cấp', metadata: providers.catalog },
        { label: 'Loại học liệu', metadata: types.catalog },
    ].filter(({ metadata }) => !['ALL_SOURCE_PLUS_LOCAL', 'MASTER_MANAGED'].includes(metadata?.completeness ?? ''))
    if (!notices.length) return null
    return <p role="status">
        {notices.map(({ label, metadata }) => `${label}: ${catalogMessage(metadata)}`).join(' ')}
    </p>
}
