from copy import deepcopy

import pytest
from conftest import completed

from data_sync_etl.application.content_bags import bag_structure
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.indexing import IndexingError, bag_of_words, validate_vector, word_windows
from data_sync_etl.domain.keywords import UnigramKeywords
from data_sync_etl.main import create_app
from tests.indexing_support import setup_index, structured_projection
from tests.local_admin_client import TestClient


def test_word_windows_unicode_offsets_overlap():
    text = "  Tiếng Việt có dấu\n học liệu số và English!   cuối "
    chunks = list(word_windows(text, 4, 1))
    assert all(text[a:b] == value for a, b, value in chunks)
    assert chunks[-1][2].endswith("cuối")
    assert chunks[0][2].split()[-1] == chunks[1][2].split()[0]
    assert list(word_windows("", 4, 1)) == []
    assert len(list(word_windows("ngắn", 4, 1))) == 1
    assert len(list(word_windows("a b c d e f g", 4, 1))) == 2
    with pytest.raises(ValueError):
        list(word_windows(text, 4, 4))
    tf = bag_of_words("HỌC học, Vie\u0323̂t! Việt _ +")
    assert tf["học"] == 2
    assert sum(tf.values()) == 4


@pytest.mark.parametrize("vector", [[0, 0, 0], [1, float("nan"), 1], [1, float("inf"), 1], [1], [True, 1, 1]])
def test_invalid_vectors(vector):
    with pytest.raises(IndexingError):
        validate_vector(vector, 3)



def ready(c, structured=True):
    index = setup_index(c)
    if structured:
        structured_projection(c)
    version, job = completed(c)
    return index, version, job


def test_queue_worker_bags_and_api_contract(container):
    index, version, job = ready(container)
    with TestClient(create_app(container)) as api:
        base = f"/api/v1/resource-versions/{version['id']}"
        preview = api.get(base + "/chunks?limit=1").json()
        assert preview["representation_status"] == "PREVIEW" and preview["total"] == 3
        assert len(preview["items"]) == 1 and "vector_ready" not in preview["items"][0]
        assert api.get(base + "/index-status").json()["index_status"] == "NOT_STARTED"
        first = api.post(base + "/index")
        assert first.status_code == 202, first.text
        assert api.post(base + "/index").json()["action"] == "IN_PROGRESS"
        assert not index.bags.rows
        assert container.worker.once("index-test")
        status = api.get(base + "/index-status").json()
        assert status["index_status"] == "READY" and status["bag_count"] == 1
        assert status["chunk_preview_count"] == 3 and status["representation_unit"] == "content_bag"
        assert api.post(base + "/index").status_code == 200
        assert api.get(base + "/chunks?limit=101").status_code == 422
        assert api.get(base + "/chunks?offset=-1").status_code == 422
        second = api.post(base + "/reindex")
        assert second.status_code == 202 and second.json()["index_run_id"] != first.json()["index_run_id"]
        assert api.get(base + "/index-status").json()["published_run_id"] is None
        assert api.post(base + "/reindex").status_code == 409
        assert index.once()
        assert len(index.bags.rows) == 1  # fixed PG root ID, not per-run history
        assert api.get(base + "/index-status").json()["published_run_id"] == second.json()["index_run_id"]
        assert api.post(base + "/reindex", json={"force": True}).status_code == 422
    bag = next(iter(index.bags.rows.values()))
    assert bag.embedding_text == " | ".join(bag.keywords)
    with container.uow() as repo:
        assert repo.get("content_units", bag.id)["unit_type"] == "CHAPTER"
        assert repo.get("ocr_jobs", job["id"])["status"] == "COMPLETED"
        assert repo.count("ocr_jobs", {}) == 1
    assert all(len(d["keywords"] or []) <= 5 for d in container.documents.units.values())


def test_flat_preview_does_not_write_then_manual_fallback(container):
    index, version, _ = ready(container, False)
    before = deepcopy(container.documents.units)
    with TestClient(create_app(container)) as api:
        base = f"/api/v1/resource-versions/{version['id']}"
        status = api.get(base + "/index-status").json()
        assert status["index_status"] == "NOT_STARTED"
        assert status['representation_mode'] == 'fallback'
        assert status["processing_status"] == "CONTENT_READY"
        chunks = api.get(base + "/chunks").json()
        assert chunks["total"] == 3 and chunks["index_run_id"] is None
        assert api.get(base + "/chunks?offset=100").json()["items"] == []
        unit = chunks["items"][0]["content_unit_id"]
        assert api.get(base + f"/chunks?content_unit_id={unit}").json()["total"] == 1
        assert api.get("/api/v1/resource-versions/missing/chunks").status_code == 404
    assert container.documents.units == before and not index.bags.rows
    with container.uow() as repo:
        assert repo.count("etl_stage_runs", {"stage": "INDEX_BUILD"}) == 0
    with TestClient(create_app(container)) as api:
        assert api.post(base + '/index').status_code == 202
        assert index.once()
        data = api.get(base + '/index-status').json()
        assert data['index_status'] == 'READY' and data['bag_count'] == 3
        assert data['representation_mode'] == 'fallback'
    with container.uow() as repo:
        units = repo.find('content_units', {'resource_version_id': version['id']})
        assert all(u['parent_unit_id'] is None and u['unit_type'] == 'PAGE_TEXT' for u in units)
        assert set(index.bags.rows) == {u['id'] for u in units}


@pytest.mark.parametrize("failure", ["missing_model", "dimension", "schema"])
def test_runtime_blocked_and_preview_stays_available(container, failure):
    index, version, _ = ready(container)
    if failure == "missing_model":
        index.embedding.enabled = False
    elif failure == "dimension":
        index.bags.dimension = 256
    else:
        def fail():
            raise IndexingError("BLOCKED_INDEX_SCHEMA", 503)
        index.bags.ensure_ready = fail
    with pytest.raises(IndexingError):
        index.enqueue(version["id"])
    assert index.status(version["id"])["index_status"] == "BLOCKED_RUNTIME"
    assert index.chunks(version["id"])["total"] == 3


@pytest.mark.parametrize("vector", [[0., 0., 0.], [float("nan"), 0., 1.], [1., 2.]])
def test_bad_embedding_never_published(container, vector):
    index, version, _ = ready(container)
    index.enqueue(version["id"])
    index.embedding.embed = lambda _: vector
    assert index.once()
    status = index.status(version["id"])
    assert status["index_status"] == "FAILED" and not status["vector_ready"]
    assert status['error_code'] == 'INVALID_EMBEDDING'
    assert index.chunks(version["id"])["total"] == 3
    with pytest.raises(IndexingError, match="REINDEX_REQUIRED"):
        index.enqueue(version["id"])


@pytest.mark.parametrize('code', ['BLOCKED_INDEX_SCHEMA', 'BLOCKED_EMBEDDING_RUNTIME', 'INVALID_EMBEDDING'])
def test_historical_runtime_failure_allows_reindex_but_current_blocker_still_blocks(container, code):
    index, version, _ = ready(container, False)
    first = index.enqueue(version['id'])
    with container.uow() as repo:
        repo.update('etl_stage_runs', first['index_run_id'], {'status': 'FAILED', 'error_message': code})
    with TestClient(create_app(container)) as api:
        base = f"/api/v1/resource-versions/{version['id']}"
        state = api.get(base + '/index-status').json()
        assert state['index_status'] == 'FAILED' and state['error_code'] == code
        assert 'có thể tái lập chỉ mục' in state['message']
        original_ready = index.bags.ensure_ready
        def fail():
            raise IndexingError('BLOCKED_INDEX_SCHEMA', 503)
        index.bags.ensure_ready = fail
        assert api.get(base + '/index-status').json()['index_status'] == 'BLOCKED_RUNTIME'
        assert api.post(base + '/reindex').status_code == 503
        index.bags.ensure_ready = original_ready
        assert api.post(base + '/reindex').status_code == 202
        assert index.once()
        assert api.get(base + '/index-status').json()['index_status'] == 'READY'
    with container.uow() as repo:
        historical = repo.get('etl_stage_runs', first['index_run_id'])
        assert historical['status'] == 'FAILED' and historical['error_message'] == code


def test_partial_write_no_publication_and_restart(container):
    index, version, _ = ready(container)
    first = index.enqueue(version["id"])
    with container.uow() as repo:
        repo.update("etl_stage_runs", first["index_run_id"], {"status": "RUNNING"})
    assert index.once() and index.status(version["id"])["index_status"] == "READY"
    second = index.enqueue(version["id"], force=True)
    original = index.bags.upsert_bag
    def partial(doc, **kwargs):
        original(doc, **kwargs)
        raise RuntimeError("private model error")
    index.bags.upsert_bag = partial
    assert index.once()
    status = index.status(version["id"])
    assert status["index_status"] == "FAILED" and status["published_run_id"] is None
    assert index.chunks(version["id"])["total"] == 3
    with container.uow() as repo:
        assert repo.get("etl_stage_runs", second["index_run_id"])["error_message"] == "INDEX_FAILED"


def test_keyword_source_and_hierarchy_changes_stale(container):
    index, version, _ = ready(container)
    index.enqueue(version["id"])
    index.once()
    leaf = next(d for d in container.documents.units.values() if d.get("keywords"))
    leaf["keywords"] = ["changed"]
    assert index.status(version["id"])["index_status"] == "STALE"
    index.enqueue(version["id"], force=True)
    index.once()
    assert index.status(version["id"])["index_status"] == "READY"
    index.embedding.version = "new"
    assert index.status(version["id"])["index_status"] == "STALE"


@pytest.mark.parametrize("kind", ["cycle", "orphan", "cross-version"])
def test_invalid_hierarchy(kind):
    rows = [dict(id="a", resource_version_id="v", parent_unit_id=None, unit_type="CHAPTER"),
            dict(id="b", resource_version_id="v", parent_unit_id="a", unit_type="PAGE_TEXT")]
    if kind == "cycle":
        rows[0]["parent_unit_id"] = "b"
    elif kind == "orphan":
        rows[1]["parent_unit_id"] = "missing"
    else:
        rows[1]["resource_version_id"] = "other"
    with pytest.raises(DomainError):
        bag_structure(rows)


def test_keywords_deterministic_unigram_normalization():
    extractor = UnigramKeywords()
    words = extractor.extract("Toán TOÁN toán và học sinh học sinh định luật vật lý.")
    assert words == extractor.extract("Toán TOÁN toán và học sinh học sinh định luật vật lý.")
    assert len(words) == len(set(words)) <= 5 and "toán" in words and "và" not in words
    assert extractor.extract("và là của 123") == []


@pytest.mark.parametrize('structured', [False, True])
@pytest.mark.parametrize('automatic', [False, True])
def test_numeric_leaf_preserved_without_blocking_index(container, structured, automatic):
    index = setup_index(container)
    if structured:
        structured_projection(container)
    container.settings.index_auto_enabled = automatic
    version, _ = completed(container, texts=['A1: Resource | B1: Total', 'A2: 120 | B2: 450'])
    if not automatic:
        assert index.status(version['id'])['index_status'] == 'NOT_STARTED'
        index.enqueue(version['id'])
        assert index.once()
    status = index.status(version['id'])
    assert status['index_status'] == 'READY' and status['bag_count'] == 1
    assert index.chunks(version['id'])['total'] >= 2
    numeric = next(doc for doc in container.documents.units.values() if '120' in doc['text'])
    assert numeric.get('keywords') in (None, [])
    assert numeric['is_current']
    assert all(bag.keywords and bag.embedding_text for bag in index.bags.rows.values())
    index.enqueue(version['id'], force=True)
    assert index.once()
    assert index.status(version['id'])['index_status'] == 'READY'
    assert len(index.bags.rows) == 1


def test_only_numeric_content_does_not_create_fake_bag(container):
    index = setup_index(container)
    version, _ = completed(container, texts=['A2: 120 | B2: 450'])
    with pytest.raises(IndexingError, match='LEAF_KEYWORDS_UNAVAILABLE'):
        index.enqueue(version['id'])
    assert index.status(version['id'])['processing_status'] == 'CONTENT_READY'
    assert index.chunks(version['id'])['total'] == 1
    assert not index.bags.rows


def test_source_race_and_timeout_do_not_publish(container):
    index, version, _ = ready(container)
    index.enqueue(version['id'])
    original = index.embedding.embed
    def mutate(text):
        with container.uow() as repo:
            root = repo.find('content_units', {'resource_version_id': version['id'], 'unit_type': 'CHAPTER'})[0]
            repo.update('content_units', root['id'], {'title': 'Concurrent changed title'})
        return original(text)
    index.embedding.embed = mutate
    index.once()
    assert not index.status(version['id'])['vector_ready']
    index.embedding.embed = original
    index.enqueue(version['id'], force=True)
    index._deadline = lambda _: (_ for _ in ()).throw(IndexingError('INDEX_LIMIT_EXCEEDED'))
    index.once()
    assert index.status(version['id'])['index_status'] == 'FAILED'


def test_multi_root_partial_write_never_ready(container):
    index, version, job = ready(container)
    with container.uow() as repo:
        leaves = repo.find('content_units', {'resource_version_id': version['id'], 'unit_type': 'PAGE_TEXT'})
        body = repo.get('content_unit_texts', leaves[0]['id'])
        root = repo.insert('content_units', dict(resource_version_id=version['id'], unit_type='CHAPTER',
            title='Second synthetic chapter', sequence_no=99, review_status='UNREVIEWED', retrieval_eligible=False))
        repo.insert('content_unit_texts', dict(content_unit_id=root['id'], page_result_id=body['page_result_id'], text=''))
        repo.update('content_units', leaves[0]['id'], {'parent_unit_id': root['id']})
        generation = len(repo.find('etl_stage_runs', {'stage': 'MONGO_PROJECTION', 'resource_version_id': version['id']}))
    container.mongo_projector.project(version['id'], job['id'], generation=generation)
    index.enqueue(version['id'])
    original = index.bags.upsert_bag
    def partial(doc, **kwargs):
        if index.bags.rows:
            raise RuntimeError('Second root failure')
        original(doc, **kwargs)
    index.bags.upsert_bag = partial
    index.once()
    assert len(index.bags.rows) == 1
    assert index.status(version['id'])['index_status'] == 'FAILED'
    assert not index.status(version['id'])['bags']


@pytest.mark.parametrize("structured,enabled", [(False, False), (False, True), (True, False), (True, True)])
def test_auto_new_projection_only_no_backfill(container, structured, enabled):
    index = setup_index(container)
    if structured:
        structured_projection(container)
    container.settings.index_auto_enabled = enabled
    version, _ = completed(container)
    with container.uow() as repo:
        runs = index._runs(repo, version["id"])
        assert len(runs) == int(enabled)
        if enabled:
            assert runs[0]["status"] == 'COMPLETED'
            index.enqueue_auto(repo, version["id"])
            assert len(index._runs(repo, version["id"])) == 1
    assert index.status(version["id"])["processing_status"] == "CONTENT_READY"
    container.settings.index_auto_enabled = True
    assert not index.once()
    with container.uow() as repo:
        assert len(index._runs(repo, version["id"])) == len(runs)


def test_preview_paging_unicode_identity_and_limits(container):
    index, version, _ = ready(container, False)
    index.settings.chunk_size_words, index.settings.chunk_overlap_words = 20, 4
    text = "Học liệu tiếng Việt có dấu 😀\n" * 500
    with container.uow() as repo:
        units = repo.find("content_units", {"resource_version_id": version["id"]}, order=("sequence_no",))
        for u in units:
            repo.update("content_unit_texts", u["id"], {"text": text})
            container.documents.units[u["id"]]["text"] = text
    first = index.chunks(version["id"], limit=100)
    second = index.chunks(version["id"], offset=100, limit=100)
    assert len(second["items"]) == 100
    assert first["items"][0]["id"] == index.chunks(version["id"])["items"][0]["id"]
    for chunk in first["items"] + second["items"]:
        assert text[chunk["char_start"]:chunk["char_end"]] == chunk["text"]
    assert index.chunks(version["id"], content_unit_id=units[0]["id"])["items"][0]["id"] != index.chunks(version["id"], content_unit_id=units[1]["id"])["items"][0]["id"]
    index.settings.index_max_chunks = 1
    with pytest.raises(IndexingError, match="INDEX_LIMIT_EXCEEDED"):
        index.chunks(version["id"])


def test_inactive_guards_and_openapi(container):
    index, version, _ = ready(container)
    app = create_app(container)
    paths = app.openapi()["paths"]
    for suffix, method in (("index", "post"), ("chunks", "get"), ("index-status", "get"), ("reindex", "post")):
        assert paths[f"/api/v1/resource-versions/{{id}}/{suffix}"][method]["tags"] == ["Data Chunking and Representation"]
    with container.uow() as repo:
        repo.update("master_learning_resources", version["resource_id"], {"is_active": False})
    with TestClient(app) as api:
        for suffix in ("chunks", "index-status"):
            assert api.get(f"/api/v1/resource-versions/{version['id']}/{suffix}").status_code == 404


def test_post_routes_keep_admin_guard(container):
    _, version, _ = ready(container)
    container.settings.admin_enabled = False
    with TestClient(create_app(container)) as api:
        base = f"/api/v1/resource-versions/{version['id']}"
        assert api.get(base + '/index-status').status_code == 403
        for suffix in ('index', 'reindex'):
            assert api.post(base + '/' + suffix).status_code == 403


def test_empty_content_and_stale_projection_are_not_indexed(container):
    index, version, _ = ready(container, False)
    for doc in container.documents.units.values():
        doc['projection_generation'] += 1
    with pytest.raises(IndexingError, match='CONTENT_NOT_READY'):
        index.chunks(version['id'])
    with container.uow() as repo:
        for unit in repo.find('content_units', {'resource_version_id': version['id']}):
            repo.update('content_unit_texts', unit['id'], {'text': ''})
            doc = container.documents.units[unit['id']]
            doc['text'] = ''
            doc['projection_generation'] -= 1
    with pytest.raises(IndexingError, match='EMPTY_CONTENT'):
        index.chunks(version['id'])


def test_production_adapter_is_local_only(tmp_path):
    from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding

    model = LocalSentenceEmbedding(tmp_path / "missing", "model", "v1", 384)
    assert not model.available()
    with pytest.raises(IndexingError, match="BLOCKED_EMBEDDING_RUNTIME"):
        model.prepare()


def test_local_embedding_probe_does_not_download_and_rejects_dimension(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding

    (tmp_path / "modules.json").write_text('[{"path":""}]', encoding="utf-8")
    (tmp_path / "etl-model-revision.txt").write_text("revision-1", encoding="utf-8")
    (tmp_path / "model.safetensors").write_bytes(b"test fixture only, not a real model")
    monkeypatch.setattr("data_sync_etl.adapters.embeddings.find_spec", lambda _: True)
    calls = []

    def construct(path, **kwargs):
        calls.append(kwargs)
        assert path == str(tmp_path.resolve())
        assert kwargs == {"local_files_only": True, "device": "cpu"}
        return SimpleNamespace(get_sentence_embedding_dimension=lambda: 3,
                               encode=lambda *args, **kw: SimpleNamespace(tolist=lambda: [0.1, 0.2, 0.3]))

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=construct))
    model = LocalSentenceEmbedding(tmp_path, "test-only", "revision-1", 3)
    assert calls == [] and model.available()
    assert not model.ready()
    model.prepare()
    assert model.ready() and model.ready()
    assert len(calls) == 1
    assert model.embed("Tiếng Việt") == [0.1, 0.2, 0.3]
    wrong = LocalSentenceEmbedding(tmp_path, "test-only", "revision-1", 2)
    with pytest.raises(IndexingError, match="INVALID_EMBEDDING"):
        wrong.prepare()
    assert not wrong.ready()
