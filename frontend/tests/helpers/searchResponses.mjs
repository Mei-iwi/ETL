// Complete wire contracts, not permissive partial success payloads.
export const searchRequest = { query: 'synthetic', method: 'hybrid', filters: {}, page: 1, size: 5 }
export function searchResponse(request = searchRequest) {
    return { query: request.query.normalize('NFC').trim(), method: request.method, page: request.page, size: request.size,
        total: 1, total_pages: 1, representation_unit: 'content_bag', ready_bag_count: 1,
        ranking_algorithm: 'rrf-v1', access_scope: 'local_demo', message: 'OK', items: [{
            resource_id: 'r1', resource_title: 'Sample', resource_version_id: 'v1', version_number: 1,
            provider_name: null, resource_type_code: 'DOC', bag_id: 'b1', bag_title: null, unit_type: 'SECTION',
            representation_mode: 'fallback', matched_keywords: ['sample'], matched_bag_count: 1, score: .5,
            score_components: { bow: .5, vector: .5 }, snippet: 'Sample content', page_from: null, page_to: null,
            content_unit_id: 'u1', context_id: 'token', context_url: '/api/v1/chunks/token/context',
        }] }
}
export function contextResponse() {
    return { resource_id: 'r1', resource_title: 'Sample', resource_version_id: 'v1', version_number: 1,
        context_id: 'token', content_unit_id: 'u1', bag_id: 'b1', neighbors: 1,
        heading_path: [{ content_unit_id: 'u1', title: null, unit_type: 'SECTION' }], chunks: [{
            id: 'c1', content_unit_id: 'u1', unit_chunk_sequence: 1, char_start: 0, char_end: 10,
            page_from: null, page_to: null, text: 'Nguồn thật', selected: true,
        }] }
}
