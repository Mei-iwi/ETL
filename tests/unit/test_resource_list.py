from fastapi.testclient import TestClient

from data_sync_etl.main import create_app


def test_resource_list_pagination(container):

    container.sync.run(full=True)

    with TestClient(create_app(container)) as client:
        response = client.get(
            'api/v1/resources',
            params= {'page': 1, 'size': 2}
        )

    assert response.status_code == 200

    data = response.json()

    assert data['pagination']['page'] == 1
    assert data['pagination']['size'] == 2
    assert data['pagination']['total'] == 3
    assert data['pagination']['total_pages'] == 2

    assert len(data['items']) == 2

    for item in data['items']:
        assert 'id' in  item
        assert 'title' in item
        assert 'provider_name' in item
        assert 'provider_name' in item
        assert 'hashed_password' not in item


def test_resource_list_filters(container):
    container.sync.run(full=True)

    with TestClient(create_app(container)) as client:
        response = client.get(
            '/api/v1/resources',
            params = {
                'q': 'Resource 1',
                'subject_id': 'subject-1',
                'grade_level_id': 'grade-1',
                'resource_type_code': 'HANDOUT'
            }
        )

    assert response.status_code == 200

    data = response.json()

    assert data['pagination']['total'] == 1
    assert len(data['items']) == 1
    assert data['items'][0]['id'] == 'resource-1'


def test_resource_list_invalid_pagination(container):
    with TestClient(create_app(container)) as client:
        response = client.get(
            '/api/v1/resources',
            params = {'page': 0, 'size': 101}
        )
    assert response.status_code == 422

def test_resource_list_excludes_inactive(container):
    container.sync.run(full=True)

    with container.uow() as repo:
        repo.update(
            'master_learning_resources',
            'resource-2',
            {'is_active': False}
        )

    with TestClient(create_app(container)) as client:
        response = client.get('/api/v1/resources')

        assert response.status_code == 200

        items = response.json()['items']
        ids = {item['id'] for item in items}

        assert 'resource-2' not in ids
        assert response.json()['pagination']['total'] == 2
