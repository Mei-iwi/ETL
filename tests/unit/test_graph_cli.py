from types import SimpleNamespace

import pytest

from data_sync_etl.cli.graph import run
from data_sync_etl.cli.main import parser
from data_sync_etl.config import Settings


@pytest.mark.parametrize('args', [
    ['graph-preflight'], ['graph-preflight', '--claim-empty'],
    ['graph-sync', '--full', '--dry-run'], ['graph-sync', '--full', '--apply'],
    ['graph-diff'], ['graph-status'], ['run-graph-worker', '--once'],
])
def test_graph_commands_parse(args):
    assert parser().parse_args(args).command == args[0]


def test_sync_requires_explicit_mode_and_full():
    for args in [['graph-sync'], ['graph-sync', '--full'], ['graph-sync', '--apply'],
                 ['graph-sync', '--full', '--apply', '--dry-run']]:
        with pytest.raises(SystemExit):
            parser().parse_args(args)


def test_disabled_cli_never_connects_or_loads_container(capsys):
    assert run(SimpleNamespace(command='graph-preflight'), settings=Settings(_env_file=None)) == 1
    assert capsys.readouterr().out.strip() == '{"status": "GRAPH_DISABLED"}'
