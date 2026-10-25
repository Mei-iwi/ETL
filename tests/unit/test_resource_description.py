from unittest.mock import MagicMock

import pytest
from pymongo.errors import AutoReconnect

from data_sync_etl.adapters.mongo.foundation_store import (
    MongoMetadataStore,
    ProfileConflict,
    user_profile_revision,
)
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.mongo_documents import ResourceDescriptionDocument
from tests.unit.test_master_auth import configured as configured
from tests.unit.test_master_auth import credentials as credentials
from tests.unit.test_master_auth import login
from tests.unit.test_master_auth import master_auth as master_auth
from tests.unit.test_single_admin_auth import csrf, make_client

URL = '/api/v1/resources/resource-1/description'


@pytest.fixture
def description(master_auth):
    container = master_auth[0]
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'created_by': 'user-1', 'mongo_id': 'resource-mongo'})
    document = ResourceDescriptionDocument(_id='resource-mongo', created_at=now(), updated_at=now(),
        description='Description', tags=['school'], authors=[{'name': 'Teacher'}])
    metadata = MagicMock()
    metadata.get_resource.return_value = document
    container.metadata_documents = metadata
    return master_auth, metadata, document


def test_owner_reads_only_explicit_mapping_and_updates_extension(description):
    configured, metadata, document = description
    with make_client(configured) as api:
        assert api.get(URL).status_code == 401
        assert login(api, configured).status_code == 200
        response = api.get(URL)
        assert response.status_code == 200
        assert response.json()['editable']
        metadata.get_resource.assert_called_with('resource-mongo')
        body = {'revision': user_profile_revision(document), 'description': ' New ', 'tags': [' SCHOOL ', 'school']}
        assert api.patch(URL, json=body).status_code == 403
        response = api.patch(URL, headers=csrf(api), json=body)
        assert response.status_code == 200, response.text
        metadata.update_resource_description.assert_called_once_with('resource-mongo',
            {'description': 'New', 'tags': ['school']}, body['revision'])


def test_mass_assignment_and_invalid_input_rejected(description):
    configured, metadata, document = description
    with make_client(configured) as api:
        login(api, configured)
        for invalid in ({'mongo_id': 'foreign'}, {'created_by': 'other'}, {'title': 'New'},
                        {'authors': [{'name': '  '}]}, {'tags': ['x' * 101]}, {'authors': [{'name': 'A', 'email': 'x'}]}):
            response = api.patch(URL, headers=csrf(api), json={'revision': user_profile_revision(document), **invalid})
            assert response.status_code == 422, response.text
        metadata.update_resource_description.assert_not_called()


def test_public_reader_cannot_edit_and_owner_transfer_revokes(description, monkeypatch):
    configured, metadata, document = description
    monkeypatch.setattr('data_sync_etl.security.authorization.can_read_public_file', lambda *args: True)
    with make_client(configured) as api:
        login(api, configured)
        with configured[0].uow() as repo:
            repo.update('master_learning_resources', 'resource-1', {'created_by': 'other-user'})
            repo.insert('resource_versions', {'id': 'public-version', 'resource_id': 'resource-1',
                'version_number': 1, 'lifecycle_status': 'INGESTED', 'storage_bucket': 'input',
                'storage_object_key': 'sample.pdf', 'source_hash': 'a' * 64})
        assert api.get(URL).status_code == 401  # Ownership changes revoke the old session.
        assert login(api, configured).status_code == 200
        response = api.get(URL)
        assert response.status_code == 200, response.text
        assert not response.json()['editable']
        assert api.patch(URL, headers=csrf(api), json={
            'revision': user_profile_revision(document), 'description': 'Foreign'}).status_code == 403
        metadata.update_resource_description.assert_not_called()
        monkeypatch.setattr('data_sync_etl.security.authorization.can_read_public_file', lambda *args: False)
        assert api.get(URL).status_code == 403


def test_admin_can_edit_other_resource_and_unlinked_mapping_is_not_inferred(description):
    configured, metadata, document = description
    with make_client(configured) as api:
        login(api, configured, email='admin@etl.test')
        with configured[0].uow() as repo:
            repo.update('master_learning_resources', 'resource-1', {'created_by': 'other-user'})
        assert api.get(URL).json()['editable']
        assert api.patch(URL, headers=csrf(api), json={
            'revision': user_profile_revision(document), 'description': 'Admin'}).status_code == 200
        with configured[0].uow() as repo:
            repo.update('master_learning_resources', 'resource-1', {'mongo_id': None})
        metadata.reset_mock()
        result = api.get(URL).json()
        assert not result['editable'] and result['unavailable_reason'] == 'RESOURCE_DESCRIPTION_NOT_LINKED'
        metadata.get_resource.assert_not_called()


def test_conflict_and_mongo_error_do_not_leak_details(description):
    configured, metadata, document = description
    with make_client(configured) as api:
        login(api, configured)
        metadata.update_resource_description.side_effect = ProfileConflict('PRIVATE')
        response = api.patch(URL, headers=csrf(api), json={
            'revision': user_profile_revision(document), 'description': 'New'})
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'RESOURCE_DESCRIPTION_CHANGED'
        metadata.get_resource.side_effect = AutoReconnect('PRIVATE')
        response = api.get(URL)
        assert response.status_code == 503 and 'PRIVATE' not in response.text


def test_mongo_compare_and_swap_preserves_fields_and_detects_race():
    database = MagicMock()
    database.with_options.return_value = database
    store = MongoMetadataStore(database)
    document = ResourceDescriptionDocument(_id='resource-mongo', created_at=now(), updated_at=now(),
        description='Before', authors=[{'name': 'Author'}], tags=['school'])
    store.get_resource = MagicMock(return_value=document)
    collection = database.learning_resources
    collection.find_one.return_value = document.bson()
    collection.update_one.return_value.matched_count = 1
    token = user_profile_revision(document)
    store.update_resource_description('resource-mongo', {'tags': [' School ', 'school']}, token)
    selector, update = collection.update_one.call_args.args
    assert selector['_id'] == 'resource-mongo' and '$and' in selector
    assert set(update['$set']) == {'tags', 'updated_at'}
    assert update['$set']['tags'] == ['school']
    collection.update_one.return_value.matched_count = 0
    with pytest.raises(ProfileConflict):
        store.update_resource_description('resource-mongo', {'description': 'Race'}, token)
    with pytest.raises(ProfileConflict):
        store.update_resource_description('resource-mongo', {'description': 'Old'}, 'missing')
    with pytest.raises(DomainError):
        store.update_resource_description('resource-mongo', {'created_at': now()}, token)
