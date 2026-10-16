"""Isolated SQLite + typed Mongo doubles; never write datasets or qrels on Dev."""
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from data_sync_etl.adapters.storage.evaluation_store import EvaluationFileStore
from data_sync_etl.application.evaluation import EvaluationService
from data_sync_etl.main import create_app
from tests.unit.test_search import searchable as searchable


@pytest.mark.skipif(os.environ.get('ETL_EVALUATION_UI') != '1', reason='Opt-in Edge/real isolated API acceptance')
def test_evaluation_ui_with_real_isolated_application(searchable, tmp_path):
    c, version = searchable
    if os.environ.get('ETL_EVALUATION_REAL_MODEL') == '1':
        from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
        from data_sync_etl.config import Settings

        local = Settings()  # Only reuse model configuration, NOT database URLs.
        c.indexing.embedding = LocalSentenceEmbedding(local.index_embedding_model_path, local.index_embedding_model_id,
                                                      local.index_embedding_model_version, local.index_embedding_dimension)
        assert c.indexing.embedding.available()
        c.settings.index_embedding_dimension = 384
        c.indexing.bags.dimension = 384
        c.indexing.enqueue(version['id'], force=True)  # ONLY the isolated SQLite/Memory fixture.
        assert c.indexing.once()
    c.evaluations = EvaluationService(c.search, EvaluationFileStore(tmp_path / 'evaluation'), c.settings)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(c), host='127.0.0.1', port=port,
                                           proxy_headers=False, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        env = {**os.environ, 'ETL_EVAL_API': f'http://127.0.0.1:{port}', 'ETL_EVAL_RESOURCE': version['resource_id'],
               'ETL_EVAL_CAPTURE_DIR': str(tmp_path / 'captures')}
        result = subprocess.run(['node', 'frontend/tests/helpers/evaluationRuntimeUi.mjs'],
                                cwd=Path(__file__).parents[2], env=env, capture_output=True, text=True,
                                encoding='utf-8', timeout=180,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        assert result.returncode == 0, result.stdout + result.stderr
        assert '"ui":"PASS"' in result.stdout
        print(result.stdout.strip())
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
