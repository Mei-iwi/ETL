"""Isolated Search preflight. No Dev dotenv/network; benchmark is explicit opt-in."""
import argparse
import os
import runpy
import sys
import tempfile
import traceback
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark', action='store_true', help='Run 360 baseline searches with the real local model')
    parser.add_argument('--model-path', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if Path.cwd().resolve() != root:
        parser.error('Run from the repository root')
    sys.path.insert(0, str(root / 'src'))
    from data_sync_etl.config import Settings

    Settings.model_config['env_file'] = None
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    for key in ('TEST_DATABASE_URL', 'TEST_SOURCE_DATABASE_URL', 'TEST_MONGO_URI',
                'ETL_REQUIRE_POSTGRES', 'ETL_REQUIRE_SOURCE', 'ETL_PAGINATION_BASELINE'):
        os.environ.pop(key, None)
    targets = ['tests/unit/test_search_pagination_preflight.py']
    if args.benchmark:
        model = args.model_path or root / 'Models/paraphrase-multilingual-MiniLM-L12-v2'
        if not model.is_dir():
            parser.error('Local model folder required; no model download is permitted')
        os.environ['ETL_PAGINATION_BASELINE'] = '1'
        os.environ['ETL_PAGINATION_MODEL_PATH'] = str(model.resolve())
        os.environ['ETL_PAGINATION_BASELINE_OUTPUT'] = str(
            root / 'docs/audits/search-pagination-cache/metrics_baseline.json')
        targets.append('tests/performance/test_search_pagination_baseline.py')

    def audit(event, values):
        if event == 'open' and isinstance(values[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(values[0])).resolve()
            if path.parent == root and path.name in ('.env', '.env.source.local', '.env.local'):
                raise RuntimeError('Repository dotenv forbidden in Search preflight')
        if event == 'socket.connect':
            # Windows implements the asyncio self-pipe using a loopback pair.
            internal_pair = any(frame.name == '_fallback_socketpair' and frame.filename.endswith('socket.py')
                                for frame in traceback.extract_stack())
            if not (internal_pair and values[1][0] in ('127.0.0.1', '::1')):
                raise RuntimeError('External connections forbidden in isolated Search preflight')

    sys.addaudithook(audit)
    with tempfile.TemporaryDirectory(prefix='etl-search-pagination-preflight-') as temporary:
        sys.argv = ['scripts/run_b04_b09_tests.py', '-p', 'no:cacheprovider', '-q', '--tb=short',
                    '--basetemp', temporary, *targets]
        runpy.run_path(root / 'scripts/run_b04_b09_tests.py', run_name='__main__')


if __name__ == '__main__':
    main()
