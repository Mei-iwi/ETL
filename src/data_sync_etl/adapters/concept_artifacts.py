"""Derived immutable SQLite shards, not a source database. Local-host fencing only.

Writers are OFFLINE TEST ONLY. Runtime connections use mode=ro&immutable=1.
No Mongo/PostgreSQL imports, schema changes, raw corpus or media storage.
"""
import json
import os
import re
import shutil
import sqlite3
from contextlib import closing, contextmanager
from hashlib import sha256
from pathlib import Path
from time import monotonic, time
from uuid import uuid4

from data_sync_etl.domain.concepts import Concept
from data_sync_etl.domain.corpus_concepts import EXTRACTOR, POLICY
from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.lexical import ANALYZER_VERSION
from data_sync_etl.ports.concept_corpus import ConceptBlocked

FORMAT = 'corpus-sqlite-shards-v1'
GENERATION = re.compile(r'^[0-9a-f]{64}$')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def checksum(path):
    result = sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def no_links(path):
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ConceptBlocked('ARTIFACT_LINK_FORBIDDEN')


def sync_directory(path):
    if os.name != 'nt':
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def merge_reviewed_aliases(first, second, *, max_aliases=64, max_provenance=8):
    """Only explicit approved pairs with the same domain/sense-qualified identity."""
    if first['domain'] != second['domain']:
        raise ConceptBlocked('CONCEPT_DOMAIN_COLLISION')
    terms = {(item['language'], item['normalized']): item for item in first['terms']}
    for item in second['terms']:
        key = item['language'], item['normalized']
        if key not in terms or canonical(item) < canonical(terms[key]):
            terms[key] = item
    if len(terms) > max_aliases:
        raise ConceptBlocked('CONCEPT_ALIAS_BUDGET')
    labels = dict(first['preferred_labels'])
    for lang, label in second['preferred_labels'].items():
        labels[lang] = min(labels.get(lang, label), label)
    sources = {canonical(item): item for item in [*first['sources'], *second['sources']]}
    return {**first, 'preferred_labels': dict(sorted(labels.items())),
        'terms': [terms[key] for key in sorted(terms)],
        'sources': [sources[key] for key in sorted(sources)[:max_provenance]],
        'definition': min(first['definition'], second['definition'])}


class ArtifactStore:
    def __init__(self, root, *, create=False, mode='offline_test_only', max_bytes=512 * 1024 * 1024,
                 max_file_bytes=16 * 1024 * 1024, min_free_bytes=1024 * 1024, shards=4,
                 max_aliases=64, max_provenance=8):
        self.root = Path(root)
        if not self.root.is_absolute() or not 1 <= shards <= 16:
            raise ValueError('Absolute private root and bounded shards required')
        no_links(self.root)
        if mode != 'offline_test_only':
            raise ConceptBlocked('LIVE_PUBLISH_NOT_VERIFIED')
        self.mode, self.max_bytes, self.max_file_bytes = mode, max_bytes, max_file_bytes
        self.min_free_bytes, self.shards = min_free_bytes, shards
        if not 1 <= max_aliases <= 256 or not 1 <= max_provenance <= 64:
            raise ValueError('Invalid concept record limits')
        self.max_aliases, self.max_provenance = max_aliases, max_provenance
        if create:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, relative):
        item = Path(relative)
        if item.anchor or item.drive or '..' in item.parts or '\\' in str(relative):
            raise ConceptBlocked('ARTIFACT_PATH_FORBIDDEN')
        target = self.root / item
        no_links(target)
        return target

    def write_json(self, relative, value):
        data = canonical(value).encode('utf-8')
        if len(data) > self.max_file_bytes or shutil.disk_usage(self.root).free < self.min_free_bytes + len(data):
            raise ConceptBlocked('ARTIFACT_DISK_BUDGET')
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path(str(relative) + '.' + uuid4().hex + '.tmp')
        with temporary.open('xb') as output:
            os.chmod(temporary, 0o600)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        sync_directory(target.parent)

    def read_json(self, relative, *, default=None):
        path = self.path(relative)
        if not path.exists():
            return default
        if path.stat().st_size > self.max_file_bytes:
            raise ConceptBlocked('ARTIFACT_FILE_BUDGET')
        return json.loads(path.read_text(encoding='utf-8'))

    @contextmanager
    def writer_lock(self):
        target = self.path('publisher.lock')
        with target.open('a+b') as lock:
            if not target.stat().st_size:
                lock.write(b'0')
                lock.flush()
                os.fsync(lock.fileno())
            lock.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ConceptBlocked('CONCEPT_PUBLISHER_BUSY') from None
            try:
                yield
            finally:
                lock.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def current(self):
        pointer = self.read_json('current.json')
        if pointer is None:
            return None
        value = pointer.get('generation', '')
        if not GENERATION.fullmatch(value):
            raise ConceptBlocked('INVALID_GENERATION')
        return value

    def build(self, concepts, *, seed_hash, source_token, ledger_hash, expected_parent=None, fence=lambda: False,
              activate=False, coverage=None):
        """Streaming build. SQL staging sorts canonically without Python corpus RAM."""
        with self.writer_lock():
            if self.current() != expected_parent or not fence():
                raise ConceptBlocked('PUBLISH_FENCE_CHANGED')
            stage = self.path('staging/' + uuid4().hex)
            stage.mkdir(parents=True, mode=0o700)
            staging_db = stage / 'sort.sqlite'
            conn = sqlite3.connect(staging_db)
            try:
                conn.execute('PRAGMA page_size=16384')
                conn.execute('PRAGMA cache_size=-2048')
                conn.execute('CREATE TABLE records(id TEXT PRIMARY KEY,payload TEXT NOT NULL,bucket INTEGER NOT NULL) WITHOUT ROWID')
                payload_bytes = 0
                for index, raw in enumerate(concepts):
                    concept = raw if isinstance(raw, Concept) else Concept.model_validate(raw)
                    if (concept.review_status != 'approved' or any(t.review_status != 'approved' for t in concept.terms)
                            or concept.relations):
                        raise ConceptBlocked('ONLY_REVIEWED_ALIASES_ALLOWED')
                    value = concept.model_dump()
                    old = conn.execute('SELECT payload FROM records WHERE id=?', (concept.concept_id,)).fetchone()
                    if old:
                        value = merge_reviewed_aliases(json.loads(old[0]), value,
                            max_aliases=self.max_aliases, max_provenance=self.max_provenance)
                        Concept.model_validate(value)
                    payload = canonical(value)
                    payload_bytes += len(payload.encode('utf-8'))
                    if len(payload) > 65536 or payload_bytes > self.max_bytes:
                        raise ConceptBlocked('ARTIFACT_RELEASE_BUDGET')
                    bucket = int(sha256(concept.concept_id.encode()).hexdigest()[:8], 16) % self.shards
                    conn.execute('INSERT INTO records VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                                 (concept.concept_id, payload, bucket))
                    if index % 500 == 0 and (not fence() or shutil.disk_usage(self.root).free < self.min_free_bytes):
                        raise ConceptBlocked('BUILD_CANCELLED_OR_DISK_FULL')
                conn.commit()
                entries, count = [], 0
                for bucket in range(self.shards):
                    name = f'shard-{bucket:02}.sqlite'
                    target = stage / name
                    with closing(sqlite3.connect(target)) as shard, shard:
                        shard.execute('PRAGMA page_size=16384')
                        shard.execute('PRAGMA cache_size=-2048')
                        shard.execute('CREATE TABLE records(id TEXT PRIMARY KEY,payload TEXT NOT NULL) WITHOUT ROWID')
                        shard.execute('CREATE TABLE aliases(alias TEXT,concept_id TEXT,PRIMARY KEY(alias,concept_id)) WITHOUT ROWID')
                        for cid, payload in conn.execute('SELECT id,payload FROM records WHERE bucket=? ORDER BY id', (bucket,)):
                            shard.execute('INSERT INTO records VALUES(?,?)', (cid, payload))
                            aliases = {term['normalized'] for term in json.loads(payload)['terms']}
                            shard.executemany('INSERT INTO aliases VALUES(?,?)', ((alias, cid) for alias in sorted(aliases)))
                            count += 1
                    with target.open('r+b') as output:
                        os.fsync(output.fileno())
                    entries.append({'name': name, 'bytes': target.stat().st_size, 'sha256': checksum(target)})
                if sum(item['bytes'] for item in entries) > self.max_bytes:
                    raise ConceptBlocked('ARTIFACT_RELEASE_BUDGET')
            finally:
                conn.close()
            # The sorting DB is a private build output, not a runtime shard.
            manifest = {'format': FORMAT, 'scope': 'offline_fixture', 'seed_hash': seed_hash,
                'source_token': source_token, 'ledger_hash': ledger_hash, 'count': count, 'shards': entries,
                'extractor': EXTRACTOR, 'policy': POLICY, 'analyzer': ANALYZER_VERSION,
                'limits': {'max_aliases': self.max_aliases, 'runtime_provenance_sample': self.max_provenance},
                'coverage': dict(coverage) if coverage is not None else {'type': 'synthetic-approved-stream'},
                'parent': expected_parent, 'revocation_completeness': 'fixture_only'}
            generation = digest({key: value for key, value in manifest.items() if key != 'parent'})
            manifest.update(generation=generation, created_at=time())
            relative_stage = str(stage.relative_to(self.root)).replace('\\', '/')
            # Delete only this build's own disposable sorter, never contributions,
            # reviews or old releases. path() verifies root containment/no links.
            self.path(relative_stage + '/sort.sqlite').unlink()
            self.write_json(relative_stage + '/manifest.json', manifest)
            destination = self.path('releases/' + generation)
            destination.parent.mkdir(exist_ok=True)
            if not fence():
                raise ConceptBlocked('PUBLISH_FENCE_CHANGED')
            if not destination.exists():
                os.replace(stage, destination)
                sync_directory(destination.parent)
            release = self.load(generation)
            if not fence():
                raise ConceptBlocked('PUBLISH_FENCE_CHANGED')
            if activate:
                self.write_json('current.json', {'generation': generation})
            return release.manifest

    def activate(self, generation, *, fence):
        with self.writer_lock():
            release = self.load(generation)
            if not fence(release.manifest):
                raise ConceptBlocked('PUBLISH_FENCE_CHANGED')
            self.write_json('current.json', {'generation': generation})

    def load(self, generation=None):
        generation = generation or self.current()
        if not generation or not GENERATION.fullmatch(generation):
            raise ConceptBlocked('INVALID_GENERATION')
        folder = self.path('releases/' + generation)
        manifest = self.read_json('releases/' + generation + '/manifest.json')
        if not manifest or manifest.get('format') != FORMAT or manifest.get('generation') != generation:
            raise ConceptBlocked('INVALID_MANIFEST')
        if (manifest.get('extractor') != EXTRACTOR or manifest.get('policy') != POLICY
                or manifest.get('analyzer') != ANALYZER_VERSION):
            raise ConceptBlocked('ARTIFACT_POLICY_MISMATCH')
        logical = {key: value for key, value in manifest.items() if key not in ('generation', 'created_at', 'parent')}
        if digest(logical) != generation or not 1 <= len(manifest.get('shards', [])) <= 16:
            raise ConceptBlocked('MANIFEST_HASH_MISMATCH')
        size = 0
        for item in manifest['shards']:
            if not re.fullmatch(r'shard-\d{2}\.sqlite', item['name']):
                raise ConceptBlocked('ARTIFACT_PATH_FORBIDDEN')
            target = self.path('releases/' + generation + '/' + item['name'])
            size += target.stat().st_size
            if target.stat().st_size != item['bytes'] or checksum(target) != item['sha256'] or size > self.max_bytes:
                raise ConceptBlocked('SHARD_HASH_MISMATCH')
        return Release(folder, manifest)

    def rollback(self, generation, *, fence):
        with self.writer_lock():
            release = self.load(generation)
            if not fence(release.manifest['source_token']):
                raise ConceptBlocked('ROLLBACK_PRIVACY_FENCE')
            self.write_json('current.json', {'generation': generation})


class Release:
    def __init__(self, folder, manifest):
        self.folder, self.manifest = folder, manifest

    def lookup(self, aliases, *, max_records=32, budget_ms=20):
        aliases = sorted(set(aliases))
        if not aliases:
            return []
        if len(aliases) > 128 or not 1 <= max_records <= 128:
            raise ConceptBlocked('LOOKUP_BUDGET')
        started, result = monotonic(), {}
        for shard in self.manifest['shards']:
            target = self.folder / shard['name']
            no_links(target)
            conn = sqlite3.connect(target.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=0.1)
            try:
                conn.set_progress_handler(lambda: int((monotonic() - started) * 1000 > budget_ms), 100)
                rows = conn.execute('SELECT DISTINCT r.id,r.payload FROM aliases a JOIN records r ON r.id=a.concept_id '
                    'WHERE a.alias IN (' + ','.join('?' for _ in aliases) + ') ORDER BY r.id LIMIT ?', [*aliases, max_records + 1])
                for cid, payload in rows:
                    result[cid] = json.loads(payload)
                    if len(result) > max_records:
                        raise ConceptBlocked('LOOKUP_BUDGET')
                if (monotonic() - started) * 1000 > budget_ms:
                    raise ConceptBlocked('LOOKUP_DEADLINE')
            finally:
                conn.close()
        return [result[key] for key in sorted(result)]
