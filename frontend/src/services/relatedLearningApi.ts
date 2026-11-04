export type RelatedLearningItem = {
    resource_id: string
    title: string
    reason_label: string
    url: string
}

const reasons = new Set(['Cùng môn học', 'Cùng khối lớp', 'Cùng môn học và khối lớp'])

export async function loadRelatedLearning(resourceId: string, signal?: AbortSignal): Promise<RelatedLearningItem[]> {
    try {
        const response = await fetch(`/api/v1/resources/${encodeURIComponent(resourceId)}/related-learning`, {
            credentials: 'same-origin', cache: 'no-store', signal,
        })
        if (!response.ok) return []
        const data: unknown = await response.json()
        if (!data || typeof data !== 'object' || !('items' in data) || !Array.isArray(data.items)
            || data.items.length > 3) return []
        const seen = new Set<string>()
        const items: RelatedLearningItem[] = []
        for (const item of data.items) {
            if (!item || typeof item.resource_id !== 'string' || !item.resource_id || item.resource_id === resourceId
                || seen.has(item.resource_id) || typeof item.title !== 'string' || !item.title.trim()
                || !reasons.has(item.reason_label) || item.url !== `/library/${encodeURIComponent(item.resource_id)}`) return []
            seen.add(item.resource_id)
            items.push({ resource_id: item.resource_id, title: item.title, reason_label: item.reason_label, url: item.url })
        }
        return items
    } catch {
        // Optional suggestions never block the primary detail or file view.
        return []
    }
}
