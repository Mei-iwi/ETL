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

    def describe(self) -> dict[str, object]:
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
                    'enabled': False,
                    'size_words': self.chunk_size_words,
                    'overlap_words': self.chunk_overlap_words
                },
            },
            'representations': {
                'bag_of_words': False,
                'vector': {
                    'enabled': False,
                    'method': None,
                    'semantic': False,
                    'dimensions': self.vector_dimension
                },
            },
            'retrieval': {
                'modes': [],
                'filters': [],
            },
            'storage': {
                'metadata': self.metadata_backend,
                'content_and_index': 'mongodb' if self.mongodb_enabled else self.metadata_backend,
                'files': 'local_filesystem',
                'mongodb_enabled': self.mongodb_enabled,
            }
        }
