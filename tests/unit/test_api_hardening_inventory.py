import copy
import json

from fastapi import FastAPI
from fastapi.routing import APIRoute

from data_sync_etl.api.routes import router
from scripts.audit_api_hardening import ROOT, inventory

LEGACY_API_TAGS = {
    ("/admin/resources/{id}/versions/ingest", "post"): "Learning Resource Ingestion and Management",
    ("/admin/resource-versions/{id}/ocr-jobs", "post"): "Multimedia Data Processing and Normalization",
    ("/admin/resource-versions/{id}/postprocess", "post"): "Multimedia Data Processing and Normalization",
    ("/admin/resource-versions/{id}/etl-status", "get"): "Multimedia Data Processing and Normalization",
    ("/admin/resource-versions/{id}/content-units", "get"): "Multimedia Data Processing and Normalization",
}


def canonical_schema(value):
    """Normalize equivalent JSON Schema spellings across supported framework versions."""
    if isinstance(value, list):
        return [canonical_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: canonical_schema(item) for key, item in value.items()}
    # Framework versions use either standard reason phrase for the same HTTP 422.
    # Preserve the status, schema and all business descriptions exactly.
    validation_response = result.get('422')
    if isinstance(validation_response, dict) and validation_response.get('description') in (
        'Unprocessable Entity', 'Unprocessable Content',
    ):
        validation_response['description'] = 'Unprocessable Content'
    if result.get('type') == 'object':
        result.setdefault('additionalProperties', True)
    if result.get('type') == 'string' and result.get('contentMediaType') == 'application/octet-stream':
        result.pop('contentMediaType')
        result.setdefault('format', 'binary')
    return result


def test_inventory_covers_every_business_method_and_hidden_alias():
    rows, schema = inventory()
    actual = {(r["method"], r["path"]) for r in rows}
    expected = {(m, r.path) for r in router.routes if isinstance(r, APIRoute) for m in r.methods}
    assert expected <= actual
    assert len(actual) == len(rows)
    assert ("POST", "/admin/sync/master") in actual
    app = FastAPI()
    app.include_router(router)
    assert schema["paths"] == app.openapi()["paths"]
    assert all(r["decision"] == "KEEP" for r in rows)
    assert any(r["frontend_calls"] for r in rows)
    assert any(r["test_script_calls"] for r in rows)


def test_schema_baseline_and_register_do_not_approve_pending_schema():
    schema = json.loads((ROOT / "docs/audits/erd-contract/contract_snapshot.json").read_text(encoding="utf-8"))
    assert [len(schema["source"]), len(schema["master"]), len(schema["mongo"]["validators"])] == [15, 18, 5]
    register = json.loads((ROOT / "docs/audits/master-migration/decisions.json").read_text(encoding="utf-8"))
    assert register["approved_schema_changes"] == []
    assert register["schema_policy"] == "PRESERVE"


def test_openapi_keep_routes_and_models_match_g0_baseline():
    _, current = inventory()
    before = json.loads((ROOT / "docs/audits/api-hardening/openapi_before.json").read_text(encoding="utf-8"))
    # Keep the snapshot frozen; account only for approved prefix/tag changes.
    for (path, method), tag in LEGACY_API_TAGS.items():
        new_path = path.replace("/admin/", "/api/v1/", 1)
        old_operations = before["paths"].pop(path)
        assert path not in current["paths"]
        assert current["paths"][new_path][method]["tags"] == [tag]
        if new_path not in before["paths"]:
            old_operations[method]["tags"] = [tag]
            old_operations[method]["operationId"] = current["paths"][new_path][method]["operationId"]
            before["paths"][new_path] = old_operations
    # SOURCE-reference adds one explicitly authorized read-only capability route.
    source_info = current['paths'].pop('/api/v1/resource-versions/{id}/source-info')
    assert set(source_info) == {'get'}
    assert source_info['get']['tags']
    assert set(current['paths'].pop('/api/v1/system/health')) == {'get'}
    # Approved SOURCE previews, Search model lifecycle and evaluation history.
    for path, methods in [('/api/v1/search/model-status', {'get'}),
                          ('/api/v1/search/model-retry', {'post'})]:
        assert set(current['paths'].pop(path)) == methods
    assert current['paths']['/api/v1/evaluations/runs'].pop('get')['tags']
    preview = current['paths']['/api/v1/resource-versions/{id}/file']['get']['parameters'][-1]
    assert preview['name'] == 'view' and preview['schema']['enum'] == ['file', 'text', 'preview']
    preview['schema']['enum'] = ['file', 'text']
    assert canonical_schema(current["paths"]) == canonical_schema(before["paths"])
    # B09 user-approved additive metadata only. All legacy model contracts and
    # required fields remain exact; keep the historical G0 snapshot frozen.
    compatible = copy.deepcopy(current["components"])
    assert set(compatible['schemas'].pop('RunListResponse')['properties']) == {'items', 'total', 'page', 'size', 'total_pages'}
    assert compatible['schemas'].pop('RunListItem')['properties']
    scope = compatible['schemas']['RunDetailResponse']['properties']['scope']
    assert scope.pop('enum') == ['local_demo', 'authenticated_admin', 'authenticated_user']
    scope['const'] = 'local_demo'
    for name in ('SearchResponse', 'ContextResponse'):
        access = compatible['schemas'][name]['properties']['access_scope']
        assert access['enum'] == ['public', 'local_demo', 'authenticated_admin', 'authenticated_user']
        access['enum'] = ['public', 'local_demo']
    additions = compatible["schemas"].pop("CatalogMetadata")
    assert set(additions["properties"]) == {"identity_overlaps", "source_status", "completeness"}
    for name in ("ProviderListResponse", "ResourceTypeListResponse", "GradeLevelListResponse"):
        model = compatible["schemas"][name]
        assert "catalog" not in model.get("required", [])
        assert "catalog" in model["properties"]
        model["properties"].pop("catalog")
    # Full-content BM25 adds optional corpus readiness; preserve every legacy
    # response field and the frozen historical snapshot exactly.
    search = compatible['schemas']['SearchResponse']
    # Chunk Semantic adds optional model/index diagnostics; legacy fields and
    # the frozen historical snapshot retain their original contracts.
    assert 'vector_index' not in search.get('required', [])
    assert search['properties'].pop('vector_index')['anyOf'] == [
        {'type': 'object', 'additionalProperties': True}, {'type': 'null'}]
    assert 'readiness' not in search.get('required', [])
    readiness = search['properties'].pop('readiness')
    assert readiness['anyOf'][0]['type'] == 'object'
    assert readiness['anyOf'][1] == {'type': 'null'}
    assert readiness['anyOf'][0]['additionalProperties']['anyOf'] == [
        {'type': 'integer'}, {'type': 'boolean'}]
    for name, kind in [('query_expansion', 'object'), ('search_fingerprint', 'string')]:
        assert name not in search.get('required', [])
        expected = {'type': kind, **({'additionalProperties': True} if kind == 'object' else {})}
        assert search['properties'].pop(name)['anyOf'] == [expected, {'type': 'null'}]
    filters = compatible['schemas']['SearchFilters']
    assert 'domain' not in filters.get('required', [])
    assert filters['properties'].pop('domain')['anyOf'] == [
        {'type': 'string', 'minLength': 1, 'maxLength': 80}, {'type': 'null'}]
    item = compatible['schemas']['SearchItem']
    assert 'can_view_file' not in item.get('required', [])
    assert item['properties'].pop('can_view_file') == {
        'type': 'boolean', 'title': 'Can View File', 'default': False}
    assert 'context_evidence' not in item.get('required', [])
    assert item['properties'].pop('context_evidence')['anyOf'][0]['enum'] == ['lexical', 'semantic', 'bag']
    assert 'meaning_variants' not in item.get('required', [])
    assert item['properties'].pop('meaning_variants')['items']['type'] == 'string'
    assert 'lexical_evidence' not in item.get('required', [])
    assert item['properties'].pop('lexical_evidence')['items'] == {
        'type': 'object', 'additionalProperties': True}
    # Optional diagnostic fields in FastAPI's validation envelope vary by version.
    # Business models, required fields and actual validation rules remain exact.
    for components in (compatible, before['components']):
        validation = components['schemas']['ValidationError']
        for name in ('ctx', 'input'):
            assert name not in validation.get('required', [])
            validation['properties'].pop(name, None)
    assert canonical_schema(compatible) == canonical_schema(before["components"])


def test_all_documented_business_operations_have_explicit_non_default_tags():
    _, schema = inventory()
    for operations in schema["paths"].values():
        for method, operation in operations.items():
            if method in {"get", "post", "put", "patch", "delete", "head", "options"}:
                assert operation.get("tags")
                assert "default" not in operation["tags"]
    assert "/admin/sync/master" not in schema["paths"]
