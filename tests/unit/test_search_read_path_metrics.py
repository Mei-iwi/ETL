"""Assert physical trace invariants, separately opt-in; no database connections."""

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('ETL_READPATH_VERIFY_METRICS') != '1',
    reason='Explicit physical audit artifacts required')


@pytest.fixture(scope='module')
def reports():
    folder = Path(__file__).resolve().parents[2] / 'docs/audits/search-read-path'
    return [json.loads((folder / name).read_text(encoding='utf-8'))
            for name in ('baseline_metrics.json', 'baseline_completion.json')]


def test_actual_db_counts_match_source_n_plus_one(reports):
    checked = 0
    for report in reports:
        for row in report['requests']:
            if row['case'] != 'aa' or not row['profiler']:
                continue
            resources, units = row['corpus_resources'], row['corpus_units']
            assert row['candidate_count'] == units
            assert row['hydration_count'] == units
            assert row['snapshot_unit_visits'] == 2 * units
            assert row['PG_exec_count'] == 2 + 2 * (8 * resources + units)
            assert row['Mongo_cmd_count'] == 7 + 4 * resources + 6 * units
            assert row['total_results'] == resources
            checked += 1
    assert checked >= 36


def test_public_denied_and_cache_off(reports):
    checked = 0
    for report in reports:
        assert report['cache'] == 'OFF'
        for row in report['requests']:
            assert row['cache_status'] == 'OFF'
            if row['profiler'] and row['case'] in ('public', 'denied'):
                assert row['PG_exec_count'] == row['Mongo_cmd_count'] == 0
                assert row['candidate_count'] == 0 and row['embed_ms'] == 0
                assert row['response_status'] == (403 if row['case'] == 'denied' else 200)
                checked += 1
    assert checked >= 12


def test_paging_retains_global_reads_and_privacy(reports):
    pages = set()
    for report in reports:
        for row in report['requests']:
            if row['case'] == 'paging' and row['profiler'] and row['response_status'] == 200:
                assert row['candidate_count'] == row['corpus_units']
                assert row['PG_exec_count'] == 2 + 2 * (8 * row['corpus_resources'] + row['corpus_units'])
                pages.add((row['page'], row['page_size']))
            for operation in row['operations']:
                assert 'statement' not in operation and 'parameters' not in operation
                assert 'document' not in operation and 'embedding' not in operation
                assert len(operation['shape']) == len(operation['exact']) == 64
    assert {(1, 1), (2, 1), (101, 50)} <= pages
