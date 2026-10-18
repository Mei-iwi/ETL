export type EvaluationMethod = 'bow' | 'vector' | 'hybrid'
export type Label = { item_id: string; grade: number }
export type EvaluationQuery = { query_id: string; query: string; relevance: Label[] }
export type DatasetInput = {
    name: string; description: string; relevance_unit: 'resource'; ground_truth_confirmed: boolean
    queries: EvaluationQuery[]
}
export type DatasetCreated = {
    dataset_id: string; name: string; number_of_queries: number; relevant_items: number
    dataset_hash: string; validation: { warnings: string[] }
}
export type RunInput = {
    dataset_id: string; methods: EvaluationMethod[]; k_values: number[]; max_results: number; warmup_queries: number
}
export type RunCreated = { run_id: string; dataset_id: string; status: string; warnings: string[] }
export type EvaluationRunList = {
    items: { id: string; dataset_id: string; dataset_name: string; status: string; created_at: string
        finished_at: string | null; duration_ms: number | null; query_count: number }[]
    page: number; size: number; total: number; total_pages: number
}
export type EvaluationProgress = { completed: number; total: number; percent: number; phase: string; run_id?: string }
export type Metric = { value: number | null; valid_queries: number; excluded_queries: number }
export type MethodSummary = {
    successful_queries: number; failed_queries: number
    latency_ms: { mean: number | null; p50: number | null; p95: number | null }
    metrics: Record<string, Record<'precision' | 'recall' | 'mrr' | 'ndcg', Metric>>
}
export type Observation = {
    query_id: string; method: EvaluationMethod; latency_ms: number | null; error_code: string | null
    ranked: { resource_id: string; resource_version_id: string; bag_id: string; score: number; score_components: Record<string, number> }[]
    metrics: Record<string, Record<string, number | null>>
}
export type EvaluationRun = {
    id: string; dataset_id: string; dataset_name: string; dataset_hash: string; status: string
    created_at: string; finished_at: string | null; duration_ms?: number; warnings: string[]
    params: RunInput; relevance_unit: string; relevance_threshold: number
    corpus_fingerprint: string; snapshot: { model: { id: string; version: string; dimension: number }; search_version: string; policy_version: string }
    queries: EvaluationQuery[]; summary: Partial<Record<EvaluationMethod, MethodSummary>>
    observations: Observation[]; pagination: { offset: number; limit: number; total: number }
}
