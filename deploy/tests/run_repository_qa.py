"""Read-only repository QA in Docker. Never starts or connects to business databases."""

import argparse
import subprocess
from pathlib import Path
from time import time_ns


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='etl-db-auth-qa:repository')
    parser.add_argument('--full', action='store_true', help='Run all unit/deployment cases instead of affected cases')
    parser.add_argument('--log', type=Path, default=Path('.test-tmp/database-auth/repository-docker.log'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    git_dir = root / '.git'
    if not git_dir.is_dir():
        parser.error('A Git checkout with a .git directory is required; worktree metadata mounts need separate review.')
    tests = ['/app/tests/unit', '/app/deploy/tests'] if args.full else [
        '/app/tests/unit/test_api_hardening_inventory.py',
        '/app/tests/unit/test_favicon.py',
        '/app/tests/unit/test_migration_governance.py',
        '/app/tests/unit/test_database_auth.py',
        '/app/deploy/tests/test_auth_deployment.py',
        '/app/deploy/tests/test_native_runtime.py',
        '/app/tests/unit/test_file_preview.py::test_real_libreoffice_pdf',
    ]
    command = ['docker', 'run', '--rm', '--network', 'none', '--read-only',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
        '--tmpfs', '/tmp:rw,uid=10001,gid=10001,mode=0700',
        '--tmpfs', '/run/etl:rw,uid=10001,gid=10001,mode=0700',
        '--mount', f'type=bind,source={git_dir},target=/app/.git,readonly',
        '-e', 'ETL_DOCKER_NATIVE_TEST=1', '-e', 'ETL_TEST_OFFICE_PDF_EXECUTABLE=/usr/bin/libreoffice',
        '--entrypoint', 'python', args.image,
        '-m', 'pytest', *tests, '-q', '-rs', '--tb=short', '-o', 'cache_dir=/tmp/qa-cache',
        '--basetemp=/tmp/repository-qa']
    args.log.parent.mkdir(parents=True, exist_ok=True)
    if args.log.exists():
        args.log.rename(args.log.with_name(f'{args.log.stem}-{time_ns()}{args.log.suffix}'))
    with args.log.open('w', encoding='utf-8') as output:
        result = subprocess.run(command, cwd=root, stdout=output, stderr=subprocess.STDOUT, check=False)
    print(f'Repository Docker QA exit={result.returncode}; evidence: {args.log}')
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
