from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, Response

from data_sync_etl.api import routes
from data_sync_etl.domain.search import SearchError


def test_source_processing_checks_authorization_before_start(monkeypatch):
    repo = Mock()
    repo.get.side_effect = lambda table, _id: ({'resource_id': 'r1'} if table == 'resource_versions'
                                                else {'source_origin': 'POSTGRESQL'})

    @contextmanager
    def uow():
        yield repo

    search = SimpleNamespace(authorize_source_processing_version=Mock(side_effect=SearchError('FILE_NOT_FOUND', 404)))
    process = SimpleNamespace(run=Mock())
    container = SimpleNamespace(uow=uow, search=search, process_version=process)
    monkeypatch.setattr(routes, 'services', lambda _request: container)
    monkeypatch.setattr(routes, 'search_access', lambda _request: 'authenticated-access')
    with pytest.raises(HTTPException) as error:
        routes.process_resource_version('v1', SimpleNamespace(), Response())
    assert error.value.status_code == 404
    search.authorize_source_processing_version.assert_called_once_with('v1', access='authenticated-access')
    process.run.assert_not_called()
