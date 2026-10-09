from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class CapabilityService:
    chunk_size_words: int
    chunk_overlap_words: int
    vector_dimension: int
    ocr_engine: str

    def describe(self) -> dict[str, object]:
        return {
            'api_version': 'v1',
            'formats': [
                {
                    'extension': '.pdf',
                    'mime_type': 'application/pdf',
                    'native_text_extractioin': True,
                }
            ], 
            'master_sync': {
                'source': 'json_fixture',
                'streams': [
                    'resources',
                    'users',
                    'grades',
                    'subjects',
                ], 
                'modes': ['full', 'incremental'],
            }, 
            'processing': {
                'scanned_pdf_ocr': False,
                'fake_ocr_demo': self.ocr_engine == 'fake',
                'chunking': {
                    'enabled': True,
                    'size_words': self.chunk_size_words,
                    'overlap_words': self.chunk_overlap_words
                },
            },
            'representations': {
                'bag_of_words': True,
                'vector': {
                    'enabled': True,
                    'method': 'feature_hasing',
                    'semantic': False,
                    'dimensions': self.vector_dimension
                },
            },
            'retrieval': {
                'modes': ['keyword', 'vector', 'hybrid'],
                'filters': ['resouce_id'],
            },
            'storage': {
              'metadata': 'postgresql',
            'content_and_index': 'postgresql',
            'files': 'local_filesystem',
            'mongodb_enabled': False,
            }
        }