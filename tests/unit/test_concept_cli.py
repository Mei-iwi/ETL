import io
import json
import subprocess
import sys
from urllib.error import URLError

import pytest

from scripts import concept_dictionary as cli


def test_fetch_wikidata_saves_valid_source_and_never_overwrites(tmp_path, monkeypatch):
    payload = {'entities': {'Q2539': {'labels': {'en': {'value': 'machine learning'}}}}}
    calls = []

    def fetch(request, timeout):
        calls.append((request.full_url, timeout))
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(cli, 'urlopen', fetch)
    path = tmp_path / 'nested' / 'wikidata.json'
    assert cli.fetch_wikidata('Q2539', path)['status'] == 'downloaded_not_reviewed'
    assert json.loads(path.read_text(encoding='utf-8')) == payload
    assert calls == [('https://www.wikidata.org/wiki/Special:EntityData/Q2539.json', 30)]
    with pytest.raises(FileExistsError):
        cli.fetch_wikidata('Q2539', path)
    assert len(calls) == 1


@pytest.mark.parametrize('identifier', ['Q0', 'Q123/path', 'https://example.com', '123'])
def test_fetch_rejects_invalid_item_id_before_network(tmp_path, monkeypatch, identifier):
    monkeypatch.setattr(cli, 'urlopen', lambda *a, **k: pytest.fail('invalid ID reached network'))
    with pytest.raises(ValueError, match='--id'):
        cli.fetch_wikidata(identifier, tmp_path / 'source.json')


def test_fetch_network_failure_reports_actionable_error_without_output(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'source.json'
    monkeypatch.setattr(sys, 'argv', ['concept_dictionary.py', 'fetch-wikidata', str(path), '--id', 'Q2539'])

    def unavailable(*args, **kwargs):
        raise URLError('offline')

    monkeypatch.setattr(cli, 'urlopen', unavailable)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert 'Cannot download Wikidata' in capsys.readouterr().err
    assert not path.exists()


def test_missing_import_source_has_download_instruction_without_traceback(tmp_path):
    output = tmp_path / 'pending.json'
    result = subprocess.run([sys.executable, 'scripts/concept_dictionary.py', 'import-wikidata',
        str(tmp_path / 'missing.json'), str(output), '--domain', 'computing', '--version', 'test'],
        capture_output=True, text=True)
    assert result.returncode == 2
    assert 'File not found' in result.stderr and 'fetch-wikidata' in result.stderr
    assert 'Traceback' not in result.stderr
    assert not output.exists()


@pytest.mark.parametrize('payload', [b'not JSON', b'{"entities":{}}'])
def test_bad_source_response_is_not_saved(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(cli, 'urlopen', lambda *a, **k: io.BytesIO(payload))
    output = tmp_path / 'source.json'
    with pytest.raises(ValueError):
        cli.fetch_wikidata('Q2539', output)
    assert not output.exists()


@pytest.mark.parametrize('concept_id,reviewer,message', [
    ('wd:Q123', 'reviewer', 'available IDs:'),
    ('etl:machine_learning', 'YOUR_REVIEWER_ID', 'actual reviewer identifier'),
])
def test_review_explains_unknown_ids_and_rejects_placeholder_reviewer(tmp_path, concept_id, reviewer, message):
    output = tmp_path / 'reviewed.json'
    result = subprocess.run([sys.executable, 'scripts/concept_dictionary.py', 'review',
        'src/data_sync_etl/domain/data/concepts.json', str(output), '--concept-id', concept_id,
        '--status', 'approved', '--reviewer', reviewer], capture_output=True, text=True)
    assert result.returncode == 2
    assert message in result.stderr
    assert 'Traceback' not in result.stderr
    assert not output.exists()
