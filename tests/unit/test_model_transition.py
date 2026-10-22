from types import SimpleNamespace

import pytest
from bson import decode_file_iter

from data_sync_etl.adapters.mongo.schema import contracts
from scripts.transition_embedding_model import backup_stream, local_only


def test_backup_bson_retains_vectors_and_never_overwrites(tmp_path):
    path = tmp_path / 'backup.bson'
    records = [{'_id': 'old', 'embedding': [0.1] * 384, 'is_current': True}]
    result = backup_stream(path, records)
    with path.open('rb') as stream:
        assert list(decode_file_iter(stream)) == records
    assert result['documents'] == 1 and len(result['sha256']) == 64
    with pytest.raises(FileExistsError):
        backup_stream(path, [])


@pytest.mark.parametrize('dimension', [0, 1024, 4097, True])
def test_invalid_history_contract_rejected(dimension):
    with pytest.raises(ValueError):
        contracts(1024, historical_dimensions=[dimension])


def test_transition_requires_local_databases_and_explicit_safe_configuration():
    settings = SimpleNamespace(mongo_uri='mongodb://127.0.0.1:27018',
        database_url='postgresql+psycopg://localhost/db', index_auto_enabled=False,
        index_embedding_dimension=1024, index_historical_embedding_dimensions=[384])
    local_only(settings)
    for key, value in [('mongo_uri', 'mongodb://remote/db'), ('database_url', 'postgresql://remote/db'),
                       ('index_auto_enabled', True), ('index_historical_embedding_dimensions', [])]:
        invalid = SimpleNamespace(**{**vars(settings), key: value})
        with pytest.raises(ValueError):
            local_only(invalid)
