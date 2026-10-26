"""Read-only readiness gate for corpus-derived dictionaries. Never builds/publishes.

Default: offline structural audit. --local-readonly additionally checks the
LOCAL MASTER schema. Mongo access remains blocked without externally verified
read-only credentials; even read-only admin commands are not used.
No SOURCE connection, model load, DB mutation or raw credentials in output.
"""
import argparse
import ipaddress
import json
from pathlib import Path
from urllib.parse import urlsplit

from data_sync_etl.config import Settings
from data_sync_etl.domain.concepts import default_dictionary
from data_sync_etl.domain.mongo_documents import DOCUMENT_MODELS

READ_ACTIONS = frozenset({
    'find', 'listCollections', 'listIndexes', 'collStats', 'dbStats',
    'dbHash', 'changeStream', 'killCursors', 'planCacheRead', 'indexStats',
})


def loopback_uri(uri):
    try:
        if uri.startswith('mongodb+srv:'):
            return False
        host = urlsplit(uri).hostname
        return host == 'localhost' or ipaddress.ip_address(host or '').is_loopback
    except ValueError:
        return False


def privilege_gate(auth):
    """Role names are not proof. Empty/no-auth privilege sets fail closed too."""
    info = auth.get('authInfo', {})
    privileges = info.get('authenticatedUserPrivileges', [])
    actions = {action for grant in privileges for action in grant.get('actions', [])}
    authenticated = bool(info.get('authenticatedUsers'))
    verified = authenticated and 'find' in actions and not (actions - READ_ACTIONS)
    return {
        'authenticated': authenticated,
        'read_only_verified': verified,
        'has_find': 'find' in actions,
        'has_non_read_actions': bool(actions - READ_ACTIONS),
    }


def static_audit(settings):
    dictionary = default_dictionary()
    return {
        'environment': 'OFFLINE_STRUCTURAL',
        'seed_concepts': len(dictionary.concepts),
        'seed_fingerprint': dictionary.fingerprint,
        'custom_search_snapshot_configured': settings.search_concept_snapshot is not None,
        'expansion_enabled': settings.search_expansion.enabled,
        'mongo_contracts': {name: sorted(field.alias or key for key, field in model.model_fields.items())
                            for name, model in DOCUMENT_MODELS.items()},
        'mongo_runtime_endpoint_local': loopback_uri(settings.mongo_uri),
        'semantic_enabled': settings.semantic_enabled,
        'semantic_model_directory_exists': bool(settings.semantic_model_path and settings.semantic_model_path.is_dir()),
        'model_loaded_or_downloaded': False,
        'runtime_dictionary': 'startup seed/custom JSON; optional gated offline ConceptDictionaryProvider; live gates blocked',
        'revocation_gate': {
            'status': 'BLOCKED',
            'reason': 'No verified complete change/revocation feed for ContentUnit review/retrieval '
                      'and Mongo extension edits. MASTER resource visibility epoch alone is insufficient.',
        },
        'artifact_gate': {
            'status': 'NOT_VERIFIED',
            'reason': 'No configured concept artifact root, ACL, capacity or shared publish contract verified.',
        },
        'database_writes': False,
    }


def local_mongo_audit(settings):
    if not loopback_uri(settings.mongo_uri):
        return {'status': 'NOT_RUN', 'code': 'LOCAL_ENDPOINT_REQUIRED'}
    # Do not probe write permission, infer it from a role name, or use even a
    # read-only command on the admin namespace. Provisioning evidence is absent.
    return {'status': 'BLOCKED', 'code': 'READ_ONLY_CREDENTIAL_NOT_VERIFIED',
            'read_only_verified': False, 'corpus_or_schema_read': False,
            'mongo_connection_performed': False,
            'reason': 'Externally verified least-privilege credential required; admin commands forbidden.'}


def local_master_audit(settings):
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.engine import make_url

    url = make_url(settings.database_url)
    if not url.drivername.startswith('postgresql') or not loopback_uri(url.render_as_string(hide_password=True)):
        return {'status': 'NOT_RUN', 'code': 'LOCAL_POSTGRESQL_REQUIRED'}
    engine = None
    try:
        engine = create_engine(url, hide_parameters=True, connect_args={
            'connect_timeout': 3, 'options': '-c default_transaction_read_only=on -c statement_timeout=5000'})
        with engine.connect().execution_options(isolation_level='REPEATABLE READ', postgresql_readonly=True) as conn:
            with conn.begin():
                if conn.scalar(text('SHOW transaction_read_only')) != 'on':
                    raise RuntimeError('READ_ONLY_REQUIRED')
                inspector = inspect(conn)
                tables = ('master_learning_resources', 'resource_versions', 'content_units',
                          'etl_stage_runs', 'resource_deletion_events', 'sync_checkpoints')
                columns = {name: sorted(column['name'] for column in inspector.get_columns(name)) for name in tables}
                revision = conn.scalar(text('SELECT version_num FROM alembic_version'))
        return {'status': 'PASS', 'transaction_read_only': True, 'schema_revision': revision,
                'columns': columns, 'account_or_content_rows_read': False}
    except Exception as error:
        return {'status': 'BLOCKED', 'code': 'LOCAL_MASTER_AUDIT_UNAVAILABLE', 'error_type': type(error).__name__}
    finally:
        if engine is not None:
            engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-readonly', action='store_true', help='Local metadata/privilege audit only; never SOURCE')
    parser.add_argument('--output', type=Path, help='New private report file; refuses overwrite')
    args = parser.parse_args()
    settings = Settings()
    report = static_audit(settings)
    report['live'] = ({'mongo': local_mongo_audit(settings), 'master': local_master_audit(settings)}
                      if args.local_readonly else {'status': 'NOT_RUN'})
    report['status'] = 'BLOCKED'
    report['next_gate'] = ('Do not start corpus discovery/publishing until safe public evidence/revocation '
                           'and read-only credentials/artifact durability are verified.')
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as target:
            target.write(rendered + '\n')
    else:
        print(rendered)
    return 2  # A readiness BLOCKED result must not be reported as successful rollout.


if __name__ == '__main__':
    raise SystemExit(main())
