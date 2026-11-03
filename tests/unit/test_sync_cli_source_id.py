from types import SimpleNamespace
from unittest.mock import Mock

from data_sync_etl.cli.main import dispatch, parser


def test_sync_cli_passes_explicit_source_id_into_federated_sync():
    args = parser().parse_args(['sync-master', '--full', '--source-id', 'SRC_NEON'])
    sync = Mock(run=Mock(return_value={'users': {'status': 'COMPLETED'}}))
    assert dispatch(SimpleNamespace(sync=sync), args) == {'users': {'status': 'COMPLETED'}}
    sync.run.assert_called_once_with('all', True, source_id='SRC_NEON')
