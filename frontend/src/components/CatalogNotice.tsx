import { catalogMessage, type CatalogItems } from '../services/libraryLookupApi'

export function CatalogNotice({ providers, types }: { providers: CatalogItems<unknown>; types: CatalogItems<unknown> }) {
    return <p role="status">
        Nhà cung cấp: {catalogMessage(providers.catalog)}{' '}
        Loại học liệu: {catalogMessage(types.catalog)}
    </p>
}
