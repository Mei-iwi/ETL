"""Download sources, import offline JSON, review, and publish immutable snapshots."""
import argparse
import json
import re
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from pydantic import ValidationError

from data_sync_etl.adapters.concept_sources import conceptnet, wikidata
from data_sync_etl.domain.concepts import Snapshot, Source


def read(path):
    return Snapshot.model_validate_json(path.read_text(encoding='utf-8'))


def write(path, snapshot):
    # Never overwrite an approved snapshot or an operator's previous work.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as output:
        output.write(snapshot.model_dump_json(indent=2) + '\n')


def fetch_wikidata(entity_id, output):
    """Explicit operator action only; search never calls the network."""
    if not re.fullmatch(r'Q[1-9][0-9]*', entity_id):
        raise ValueError('--id must be a Wikidata item ID such as Q2539')
    if output.exists():
        raise FileExistsError(str(output))
    request = Request(f'https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json',
                      headers={'User-Agent': 'data-sync-etl/0.1 (dictionary import)', 'Accept': 'application/json'})
    with urlopen(request, timeout=30) as response:
        raw = response.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError('Source response exceeds the 10 MiB limit')
    payload = json.loads(raw)
    if not isinstance(payload, dict) or entity_id not in payload.get('entities', {}):
        raise ValueError('Source response does not contain the requested Wikidata entity')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as target:
        json.dump(payload, target, ensure_ascii=False, indent=2)
        target.write('\n')
    return {'source': 'wikidata', 'entity_id': entity_id, 'output': str(output), 'status': 'downloaded_not_reviewed'}


def publish(base, candidate, version):
    merged = {c.concept_id: c for c in base.concepts}
    aliases = {}
    for concept in base.concepts:
        for term in concept.terms:
            if concept.review_status == term.review_status == 'approved':
                aliases.setdefault((term.normalized, term.language), set()).add(concept.concept_id)
    for concept in candidate.concepts:
        if concept.review_status != 'approved':
            continue
        if any(t.review_status != 'approved' for t in concept.terms):
            raise ValueError('Every published term must be reviewed')
        if any(r.review_status != 'approved' for r in concept.relations):
            raise ValueError('Every published relation must be reviewed')
        previous = merged.get(concept.concept_id)
        if previous is not None and previous != concept:
            raise ValueError(f'Conflict with existing concept {concept.concept_id}; explicit manual reconciliation required')
        if previous is None and any((term.normalized, term.language) in aliases for term in concept.terms):
            raise ValueError(f'Alias conflict for {concept.concept_id}; reconcile source IDs or review homonyms manually')
        merged[concept.concept_id] = concept
    return Snapshot.model_validate({'version': version, 'concepts': [c.model_dump() for c in merged.values()]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('fetch-wikidata', help='Download one Wikidata item before offline import')
    p.add_argument('output', type=Path)
    p.add_argument('--id', required=True, dest='entity_id')
    for name in ('import-wikidata', 'import-conceptnet'):
        p = sub.add_parser(name)
        p.add_argument('input', type=Path)
        p.add_argument('output', type=Path)
        p.add_argument('--domain', required=True)
        p.add_argument('--version', required=True)
    p = sub.add_parser('validate')
    p.add_argument('input', type=Path)
    p = sub.add_parser('schema')
    p.add_argument('output', type=Path)
    p = sub.add_parser('review')
    p.add_argument('input', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--concept-id', action='append', required=True)
    p.add_argument('--status', choices=['approved', 'rejected'], required=True)
    p.add_argument('--reviewer', required=True)
    p.add_argument('--allow-relation', action='append', choices=['equivalent', 'translation', 'broader', 'narrower', 'related'], default=[])
    p = sub.add_parser('publish')
    p.add_argument('input', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--base', type=Path, required=True)
    p.add_argument('--version', required=True)
    args = parser.parse_args()
    try:
        execute(args)
    except FileNotFoundError as exc:
        parser.exit(2, f'error: File not found: {exc.filename}. For import-wikidata, first run: '
                    'python scripts/concept_dictionary.py fetch-wikidata storage/wikidata.json --id Q2539. '
                    'Run later steps only after the preceding command succeeds.\n')
    except FileExistsError:
        parser.exit(2, 'error: Output already exists; use a new output path. Existing files were not overwritten.\n')
    except URLError:
        parser.exit(2, 'error: Cannot download Wikidata. Check network access and retry; no source file was created.\n')
    except json.JSONDecodeError as exc:
        parser.exit(2, f'error: Invalid JSON at line {exc.lineno}, column {exc.colno}.\n')
    except ValidationError as exc:
        fields = ', '.join('.'.join(map(str, item['loc'])) for item in exc.errors(include_input=False)[:5])
        parser.exit(2, f'error: Invalid concept snapshot; check fields: {fields}.\n')
    except KeyError:
        parser.exit(2, 'error: Invalid source structure; expected Wikidata entities or ConceptNet edges.\n')
    except (ValueError, OSError) as exc:
        parser.exit(2, f'error: {exc}\n')


def execute(args):
    if args.command == 'fetch-wikidata':
        print(json.dumps(fetch_wikidata(args.entity_id, args.output), ensure_ascii=True))
        return
    if args.command == 'schema':
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as output:
            json.dump(Snapshot.model_json_schema(), output, ensure_ascii=False, indent=2)
        return
    if args.command.startswith('import-'):
        adapter = wikidata if args.command == 'import-wikidata' else conceptnet
        snapshot, skipped = adapter(json.loads(args.input.read_text(encoding='utf-8')),
                                    domain=args.domain, version=args.version)
        write(args.output, snapshot)
        print(json.dumps({'pending_concepts': len(snapshot.concepts), 'skipped': skipped}))
        return
    snapshot = read(args.input)
    if args.command == 'validate':
        aliases = {}
        duplicates = []
        for c in snapshot.concepts:
            seen = set()
            for t in c.terms:
                key = (t.normalized, t.language)
                if key in seen:
                    duplicates.append({'concept_id': c.concept_id, 'term': t.normalized})
                seen.add(key)
                aliases.setdefault(t.normalized, set()).add(c.concept_id)
        print(json.dumps({'fingerprint': snapshot.fingerprint, 'duplicate_terms': duplicates,
                          'ambiguous_terms': {k: sorted(v) for k, v in aliases.items() if len(v) > 1}}, ensure_ascii=True))
    elif args.command == 'review':
        if not set(args.concept_id) <= {c.concept_id for c in snapshot.concepts}:
            raise ValueError('Unknown concept ID; available IDs: ' + ', '.join(c.concept_id for c in snapshot.concepts))
        if args.reviewer in ('YOUR_REVIEWER_ID', 'ACTUAL_REVIEWER_ID'):
            raise ValueError('Replace the reviewer placeholder with the actual reviewer identifier')
        for c in snapshot.concepts:
            if c.concept_id in args.concept_id:
                c.review_status = args.status
                c.sources.append(Source(name='reviewer', identifier=args.reviewer))
                for item in [*c.terms, *c.relations]:
                    item.review_status = args.status
                    item.sources.append(Source(name='reviewer', identifier=args.reviewer))
                for relation in c.relations:
                    relation.allow_expansion = args.status == 'approved' and relation.kind in args.allow_relation
        write(args.output, Snapshot.model_validate(snapshot.model_dump()))
    else:
        write(args.output, publish(read(args.base), snapshot, args.version))


if __name__ == '__main__':
    main()
