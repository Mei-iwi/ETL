"""Validate a trusted private test export without connecting to any database."""
import argparse
import json
from pathlib import Path

from data_sync_etl.application.lexical_export import verify_export
from data_sync_etl.application.search_evaluation_snapshot import (
    SearchEvaluationSnapshot,
    replay_lexical_snapshot,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--query', help='Optional local replay; prints counts only, never private scores/text')
    args = parser.parse_args()
    try:
        if args.snapshot.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('Bounded snapshot required')
        payload = json.loads(args.snapshot.read_text(encoding='utf-8'))
        snapshot = (verify_export(payload) if payload.get('kind') == 'trusted-lexical-export-v1'
                    else SearchEvaluationSnapshot.model_validate(payload))
        hits = len(replay_lexical_snapshot(args.query, snapshot)) if args.query is not None else None
    except (OSError, ValueError):
        print(json.dumps({'status': 'BLOCKED', 'code': 'INVALID_PRIVATE_EVALUATION_SNAPSHOT'}))
        return 1
    print(json.dumps({'status': 'PASS', 'kind': snapshot.kind, 'provenance': snapshot.provenance,
                      'bags': len(snapshot.bags), 'checksum': snapshot.checksum,
                      'replay_hit_bags': hits, 'live_authorization_verified_by_this_tool': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
