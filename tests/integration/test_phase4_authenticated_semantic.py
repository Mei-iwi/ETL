"""Opt-in reuse of the normal-session PG/Mongo/BGE HTTP probe on isolated test DBs."""
import os
from pathlib import Path

import pytest

from tests.integration.test_phase3_semantic_http import (
    test_real_semantic_index_authenticated_http_and_context as run_probe,
)
from tests.unit.test_search import searchable as searchable

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(os.environ.get('ETL_PHASE4_REAL_HTTP') != '1',
    reason='Explicit isolated PostgreSQL/MongoDB and offline BGE-M3 required')]


@pytest.fixture
def container(pg_container):
    return pg_container


@pytest.mark.parametrize('searchable', [{'texts': ['Lá xanh.', 'Page two text', 'Page three text']}], indirect=True)
@pytest.mark.parametrize('vector_path', ['meaning_bag', 'semantic_chunk'])
@pytest.mark.parametrize('concurrency', [1, 2])
@pytest.mark.parametrize('accent_variants', [False, True], ids=['before', 'after'])
def test_accentless_real_authenticated_http(searchable, mongo_test_database, tmp_path,
                                           vector_path, concurrency, accent_variants):
    root = os.environ.get('ETL_PHASE4_HTTP_EVIDENCE')
    evidence = str(Path(root)/f'{"after" if accent_variants else "before"}-c{concurrency}') if root else None
    run_probe(searchable, mongo_test_database, tmp_path, vector_path, concurrency,
        diagnostic_queries=['lá xanh', 'la xanh', 'cơ sở dữ liệu', 'co so du lieu', 'green leaf',
                            'feuille verte', 'CSDL', 'CO₂', 'khong co la xanh', 'quantum banana'],
        accent_variants=accent_variants, evidence_root=evidence)
