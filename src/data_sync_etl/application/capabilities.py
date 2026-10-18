from dataclasses import dataclass

from data_sync_etl.domain.format_validation import FormatValidatorRegistry


@dataclass(frozen=True)
class CapabilityService:
    chunk_size_words: int
    chunk_overlap_words: int
    vector_dimension: int
    ocr_engine: str
    source_adapter: str = 'json'
    metadata_backend: str = 'postgresql'
    mongodb_enabled: bool = False
    auto_sync_supported: bool = False
    embedding_runtime: object | None = None

    def describe(self) -> dict[str, object]:
        runtime = self.embedding_runtime
        vector_ready = bool(runtime and runtime.ready())
        available = bool(runtime and runtime.available())
        runtime_status = ('READY' if vector_ready else 'NOT_VERIFIED' if available
                          else 'BLOCKED_EMBEDDING_RUNTIME')
        return {
            'api_version': 'v1',
            'formats': FormatValidatorRegistry().describe(),
            'master_sync': {
                'source': 'json_fixture' if self.source_adapter == 'json' else 'postgresql',
                'auto_sync_supported': self.auto_sync_supported,
                'streams': [
                    'resources',
                    'users',
                    'grades',
                    'subjects',
                ], 
                'modes': ['full', 'incremental'],
            }, 
            'processing': {
                'ocr_engine': self.ocr_engine,
                'scanned_pdf_ocr': self.ocr_engine == 'tesseract',
                'fake_ocr_demo': self.ocr_engine == 'fake',
                'normalized_content': True,
                'chunking': {
                    'enabled': True,
                    'storage': 'transient',
                    'size_words': self.chunk_size_words,
                    'overlap_words': self.chunk_overlap_words
                },
            },
            'representations': {
                'representation_unit': 'content_bag',
                'bag_of_words': True,
                'vector': {
                    'enabled': vector_ready,
                    'method': runtime.model_id if available else None,
                    'semantic': vector_ready,
                    'dimensions': runtime.dimension if available else None,
                    'implemented': True,
                    'configured': available,
                    'runtime_verified': vector_ready,
                    'runtime_scope': 'current_process',
                    'runtime_status': runtime_status,
                },
            },
            'retrieval': {
                'modes': ['bow', 'vector', 'hybrid'],
                'filters': ['subject_id', 'grade_level_id', 'resource_type_code',
                            'resource_id', 'resource_version_id'],
                # Implementation support is not corpus readiness or an access grant.
                # These checks never load the model, infer vectors or query storage.
                'methods': [
                    {'mode': mode, 'requires_embedding': mode != 'bow',
                     'runtime_status': 'NOT_REQUIRED' if mode == 'bow' else runtime_status}
                    for mode in ('bow', 'vector', 'hybrid')
                ],
                'context_supported': True,
                'pagination_supported': True,
                'access_policy': 'LOCAL_DEMO_ONLY',
                'public_search_supported': False,
            },
            'storage': {
                'metadata': self.metadata_backend,
                'content_and_index': 'mongodb' if self.mongodb_enabled else self.metadata_backend,
                'files': 'local_filesystem',
                'mongodb_enabled': self.mongodb_enabled,
            }
        }
