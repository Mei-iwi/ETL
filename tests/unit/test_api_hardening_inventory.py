"""Current API contracts; historical snapshots belong in Git history."""
from fastapi import FastAPI
from fastapi.routing import APIRoute

from data_sync_etl.api.routes import router


def test_only_versioned_business_routes_and_no_duplicate_methods():
    routes = [(method, route.path) for route in router.routes
              if isinstance(route, APIRoute) for method in route.methods]
    assert len(routes) == len(set(routes))
    assert all(path == '/health' or path.startswith('/api/v1/') for _, path in routes)
    assert ('POST', '/api/v1/master-data/sync') in routes
    for suffix, method in [('ocr-jobs', 'POST'), ('postprocess', 'POST'),
                           ('etl-status', 'GET'), ('content-units', 'GET')]:
        assert (method, '/api/v1/resource-versions/{id}/' + suffix) in routes


def test_documented_business_operations_have_tags_and_public_contracts():
    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()
    for path, operations in schema['paths'].items():
        assert not path.startswith('/admin/')
        for operation in operations.values():
            assert operation['tags'] and 'default' not in operation['tags']
    models = schema['components']['schemas']
    assert {'items', 'total', 'query', 'method'} <= models['SearchResponse']['properties'].keys()
    assert {'file', 'text', 'preview'} == set(next(parameter['schema']['enum']
        for parameter in schema['paths']['/api/v1/resource-versions/{id}/file']['get']['parameters']
        if parameter['name'] == 'view'))
