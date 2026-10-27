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
from threading import local
from time import monotonic, time
from uuid import uuid4

from data_sync_etl.adapters.concept_capacity import RootCapacity
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
                 max_aliases=64, max_provenance=8, max_root_bytes=None):
        self.root = Path(root)
        if not self.root.is_absolute() or '..' in self.root.parts or not 1 <= shards <= 16:
            raise ValueError('Absolute private root and bounded shards required')
        no_links(self.root)
        if mode != 'offline_test_only':
            raise ConceptBlocked('LIVE_PUBLISH_NOT_VERIFIED')
        self.mode, self.max_bytes, self.max_file_bytes = mode, max_bytes, max_file_bytes
        self.min_free_bytes, self.shards = min_free_bytes, shards
        if not 1 <= max_aliases <= 256 or not 1 <= max_provenance <= 64:
            raise ValueError('Invalid concept record limits')
        self.max_aliases, self.max_provenance = max_aliases, max_provenance
        self.max_root_bytes = max_root_bytes if max_root_bytes is not None else max(16 * 1024 * 1024, 4 * max_bytes)
        if self.max_root_bytes < 1024:
            raise ValueError('Invalid root quota')
        self._quota = local()
        if create:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, relative):
        item = Path(relative)
        if (item.anchor or item.drive or '..' in item.parts or '\\' in str(relative)
                or any(':' in part or part.endswith((' ', '.')) for part in item.parts)):
            raise ConceptBlocked('ARTIFACT_PATH_FORBIDDEN')
        target = self.root / item
        no_links(target)
        return target

    def preflight(self):
        """Inspection only: never changes owner/ACL or certifies live storage."""
        no_links(self.root)
        info = self.root.stat()
        free = shutil.disk_usage(self.root).free
        usage = self.capacity()
        return {'mode': self.mode, 'absolute': self.root.is_absolute(), 'links_rejected': True, 'root_capacity': usage,
            'available_bytes': free, 'capacity_ok': free >= self.min_free_bytes,
            'private_posix_mode': (info.st_mode & 0o077 == 0) if os.name != 'nt' else None,
            'same_device_root': info.st_dev, 'acl_owner_verified': False,
            'directory_fsync_supported_here': os.name != 'nt', 'distributed_fencing_verified': False,
            'live_status': 'BLOCKED_LIVE'}

    def capacity(self):
        no_links(self.root)
        return RootCapacity(self.root, self.max_root_bytes).snapshot()

    def ensure_capacity(self, reserve=0, *, refresh=False, control=False):
        usage = getattr(self._quota, 'usage', None)
        if usage is None or refresh:
            usage = self.capacity()['logical_file_bytes']
            if getattr(self._quota, 'active', False):
                self._quota.usage = usage
        limit = self.max_root_bytes if control else self.max_root_bytes - min(65536, self.max_root_bytes // 10)
        if usage + reserve > limit:
            raise ConceptBlocked('ARTIFACT_ROOT_QUOTA')

    def account_growth(self, amount):
        if getattr(self._quota, 'usage', None) is not None:
            self._quota.usage += amount

    def write_json(self, relative, value):
        data = canonical(value).encode('utf-8')
        if len(data) > self.max_file_bytes or shutil.disk_usage(self.root).free < self.min_free_bytes + len(data):
            raise ConceptBlocked('ARTIFACT_DISK_BUDGET')
        target = self.path(relative)
        old_size = target.stat().st_size if target.exists() else 0
        self.ensure_capacity(len(data), control=str(relative).startswith(('gc/', 'checkpoints/')))
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path(str(relative) + '.' + uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as output:
                os.chmod(temporary, 0o600)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            self.account_growth(len(data) - old_size)
            sync_directory(target.parent)
        finally:
            if temporary.exists():
                temporary.unlink()  # Only the UUID temporary created by this call.

    def read_json(self, relative, *, default=None):
        path = self.path(relative)
        if not path.exists():
            return default
        if path.stat().st_size > self.max_file_bytes:
            raise ConceptBlocked('ARTIFACT_FILE_BUDGET')
        return json.loads(path.read_text(encoding='utf-8'))

    @contextmanager
    def writer_lock(self):
        with self._file_lock('publisher.lock'):
            self._quota.active, self._quota.usage = True, None
            try:
                yield
            finally:
                self._quota.active, self._quota.usage = False, None

    @contextmanager
    def worker_lock(self):
        """Single local scheduler; independent of short publication transactions."""
        with self._file_lock('worker.lock'):
            yield

    @contextmanager
    def _file_lock(self, relative):
        target = self.path(relative)
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

    @contextmanager
    def reader_lease(self, generation):
        if not GENERATION.fullmatch(generation):
            raise ConceptBlocked('INVALID_GENERATION')
        # Registration and GC use the same local writer fence. A crashed process
        # releases its OS lock; no TTL is used to expire an active reader.
        relative = f'pins/{generation}/{uuid4().hex}.lease'
        with self.writer_lock():
            if not self.path('releases/' + generation).is_dir():
                raise ConceptBlocked('GENERATION_REMOVED')
            self.path(f'pins/{generation}').mkdir(parents=True, exist_ok=True)
            lock = self._file_lock(relative)
            lock.__enter__()
        try:
            yield
        finally:
            lock.__exit__(None, None, None)
            self.path(relative).unlink(missing_ok=True)

    def _pinned_locked(self, generation):
        folder = self.path('pins/' + generation)
        if not folder.exists():
            return False
        for index, lease in enumerate(folder.iterdir()):
            if index >= 1000 or not re.fullmatch(r'[0-9a-f]{32}\.lease', lease.name):
                return True  # Unknown/unbounded pin state is never permission to GC.
            try:
                with self._file_lock(f'pins/{generation}/{lease.name}'):
                    pass
            except ConceptBlocked:
                return True
        return False

    def current(self):
        pointer = self.read_json('current.json')
        if pointer is None:
            return None
        value = pointer.get('generation', '')
        if not GENERATION.fullmatch(value):
            raise ConceptBlocked('INVALID_GENERATION')
        return value

    def build(self, concepts, *, seed_hash, source_token, ledger_hash, expected_parent=None, fence=lambda: False,
              activate=False, coverage=None, reuse_shards=True):
        """Streaming build. SQL staging sorts canonically without Python corpus RAM."""
        with self.writer_lock():
            if self.current() != expected_parent or not fence():
                raise ConceptBlocked('PUBLISH_FENCE_CHANGED')
            # Conservative peak admission; actual sorter/shard growth is checked
            # again periodically. This is cooperative accounting, not OS quota.
            self.ensure_capacity(3 * self.max_bytes, refresh=True)
            parent = self.load(expected_parent).manifest if expected_parent else None
            reusable = bool(reuse_shards and parent and parent['seed_hash'] == seed_hash
                and parent['limits'] == {'max_aliases': self.max_aliases, 'runtime_provenance_sample': self.max_provenance}
                and len(parent['shards']) == self.shards)
            old_shards = {item['name']: item for item in parent['shards']} if reusable else {}
            self.last_build_stats = {'reused': 0, 'rebuilt': 0, 'linked_bytes': 0, 'copied_bytes': 0,
                'rebuilt_shard_bytes': 0, 'logical_payload_bytes_hashed': 0,
                'parent_verification_bytes': sum(item['bytes'] for item in parent['shards']) if parent else 0}
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
                    if index % 500 == 0:
                        self.ensure_capacity(refresh=True)
                conn.commit()
                entries, count = [], 0
                for bucket in range(self.shards):
                    name = f'shard-{bucket:02}.sqlite'
                    target = stage / name
                    logical, bucket_count = sha256(), 0
                    for cid, payload in conn.execute('SELECT id,payload FROM records WHERE bucket=? ORDER BY id', (bucket,)):
                        data = cid.encode() + b'\0' + payload.encode('utf-8') + b'\n'
                        logical.update(data)
                        self.last_build_stats['logical_payload_bytes_hashed'] += len(data)
                        bucket_count += 1
                    old = old_shards.get(name)
                    if old and old.get('content_sha256') == logical.hexdigest() and old.get('records') == bucket_count:
                        origin = self.path(f"releases/{expected_parent}/{name}")
                        try:
                            os.link(origin, target)
                            self.last_build_stats['linked_bytes'] += old['bytes']
                        except OSError:
                            shutil.copyfile(origin, target)
                            self.last_build_stats['copied_bytes'] += old['bytes']
                        self.last_build_stats['reused'] += 1
                    else:
                        with closing(sqlite3.connect(target)) as shard, shard:
                            shard.execute('PRAGMA page_size=16384')
                            shard.execute('PRAGMA cache_size=-2048')
                            shard.execute('CREATE TABLE records(id TEXT PRIMARY KEY,payload TEXT NOT NULL) WITHOUT ROWID')
                            shard.execute('CREATE TABLE aliases(alias TEXT,concept_id TEXT,PRIMARY KEY(alias,concept_id)) WITHOUT ROWID')
                            for cid, payload in conn.execute('SELECT id,payload FROM records WHERE bucket=? ORDER BY id', (bucket,)):
                                shard.execute('INSERT INTO records VALUES(?,?)', (cid, payload))
                                aliases = {term['normalized'] for term in json.loads(payload)['terms']}
                                shard.executemany('INSERT INTO aliases VALUES(?,?)', ((alias, cid) for alias in sorted(aliases)))
                        self.last_build_stats['rebuilt'] += 1
                        self.last_build_stats['rebuilt_shard_bytes'] += target.stat().st_size
                    count += bucket_count
                    with target.open('r+b') as output:
                        os.fsync(output.fileno())
                    entries.append({'name': name, 'bytes': target.stat().st_size, 'sha256': checksum(target),
                        'content_sha256': logical.hexdigest(), 'records': bucket_count})
                    self.ensure_capacity(refresh=True)
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
            else:
                # Only this call's UUID staging directory, never old evidence.
                for child in stage.iterdir():
                    relative = str(child.relative_to(self.root)).replace('\\', '/')
                    self.path(relative).unlink()
                stage.rmdir()
            release = self.load(generation)
            self.ensure_capacity(refresh=True)
            if not fence():
                raise ConceptBlocked('PUBLISH_FENCE_CHANGED')
            if activate:
                self._switch(generation, expected_parent)
            return release.manifest

    def activate(self, generation, *, fence):
        with self.writer_lock():
            release = self.load(generation)
            if not fence(release.manifest):
                raise ConceptBlocked('PUBLISH_FENCE_CHANGED')
            current = self.current()
            if current != generation and current != release.manifest.get('parent'):
                raise ConceptBlocked('PUBLISH_PARENT_CHANGED')
            self._switch(generation, current)

    def _switch(self, generation, expected_parent):
        if self.current() != expected_parent:
            raise ConceptBlocked('PUBLISH_PARENT_CHANGED')
        self.write_json('publish-intent.json', {'generation': generation, 'previous': expected_parent,
            'status': 'PREPARED'})
        if expected_parent and expected_parent != generation:
            self.write_json('last-good.json', {'generation': expected_parent})
        self.write_json('current.json', {'generation': generation})
        self.write_json('publish-intent.json', {'generation': generation, 'previous': expected_parent,
            'status': 'COMMITTED'})

    def recovery_status(self):
        """No automatic rollback: only an authority-checked operator can switch."""
        intent = self.read_json('publish-intent.json')
        current = self.current()
        if current:
            self.load(current)
        return {'current': current, 'intent': intent,
            'status': 'INTERRUPTED' if intent and intent.get('status') == 'PREPARED' else 'CONSISTENT',
            'automatic_rollback': False, 'live_status': 'BLOCKED_LIVE'}

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
        names = [item.get('name', '') for item in manifest.get('shards', [])]
        if (len({name.casefold() for name in names}) != len(names)
                or manifest.get('scope') != 'offline_fixture'
                or manifest.get('revocation_completeness') != 'fixture_only'):
            raise ConceptBlocked('INVALID_MANIFEST')
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
            self._switch(generation, self.current())

    def gc(self, *, retain=3, min_age_seconds=3600, grace_seconds=60, dry_run=True, now=None,
           max_catalog=1000, max_total_bytes=1024 * 1024 * 1024):
        """Two-phase derived-release GC only; default is inspection, never deletion.

        Local OS leases protect active/pinned readers. This is not distributed
        fencing and cannot be enabled on unverified network/shared storage.
        """
        if not 1 <= retain <= 100 or min_age_seconds < 0 or grace_seconds < 0:
            raise ValueError('Invalid retention policy')
        instant = time() if now is None else now
        with self.writer_lock():
            folder = self.path('releases')
            catalog = []
            if folder.exists():
                for index, item in enumerate(folder.iterdir()):
                    if index >= max_catalog or not GENERATION.fullmatch(item.name):
                        raise ConceptBlocked('GC_CATALOG_NOT_VERIFIED')
                    manifest = self.read_json(f'releases/{item.name}/manifest.json')
                    if (not manifest or manifest.get('generation') != item.name
                            or manifest.get('scope') != 'offline_fixture'):
                        raise ConceptBlocked('GC_RELEASE_NOT_VERIFIED')
                    catalog.append(manifest)
            catalog.sort(key=lambda item: (item['created_at'], item['generation']), reverse=True)
            protected = {item['generation'] for item in catalog[:retain]}
            protected.add(self.current())
            last_good = self.read_json('last-good.json')
            if last_good:
                protected.add(last_good['generation'])
            result = {'dry_run': dry_run, 'candidates': [], 'tombstoned': [], 'deleted': [], 'pinned': [],
                'release_bytes_before': sum(shard['bytes'] for item in catalog for shard in item['shards']),
                'max_total_release_bytes': max_total_bytes, 'live_status': 'BLOCKED_LIVE'}
            result['resumed_retirements'] = self._clean_trash_locked(protected, dry_run=dry_run)
            for manifest in reversed(catalog):
                generation = manifest['generation']
                if generation in protected or instant - manifest['created_at'] < min_age_seconds:
                    continue
                if self._pinned_locked(generation):
                    result['pinned'].append(generation)
                    continue
                result['candidates'].append(generation)
                if dry_run:
                    continue
                # Validate complete checksums immediately before marking/deleting.
                self.load(generation)
                marker_path = f'gc/tombstones/{generation}.json'
                marker = self.read_json(marker_path)
                expected = digest(manifest)
                if marker is None:
                    self.write_json(marker_path, {'manifest_sha256': expected, 'marked_at': instant,
                        'files': sorted({'manifest.json', *[item['name'] for item in manifest['shards']]})})
                    result['tombstoned'].append(generation)
                    continue
                if marker.get('manifest_sha256') != expected:
                    raise ConceptBlocked('GC_TOMBSTONE_MISMATCH')
                if instant - marker['marked_at'] < grace_seconds:
                    continue
                release_path = self.path('releases/' + generation)
                allowed = {'manifest.json', *[item['name'] for item in manifest['shards']]}
                children = list(release_path.iterdir())
                if {item.name for item in children} != allowed:
                    raise ConceptBlocked('GC_UNKNOWN_RELEASE_FILES')
                for child in children:
                    target = self.path(f'releases/{generation}/{child.name}')
                    if not target.is_file():
                        raise ConceptBlocked('GC_UNKNOWN_RELEASE_FILES')
                trash = self.path('gc/trash/' + generation)
                trash.parent.mkdir(exist_ok=True)
                os.replace(release_path, trash)
                sync_directory(folder)
                # If deletion is interrupted (e.g. Windows file locking), the
                # next GC resumes this owned trash tree from its durable marker.
                self._clean_trash_locked(protected, dry_run=False)
                result['deleted'].append(generation)
            deleted = set(result['deleted'])
            result['release_bytes_after'] = sum(shard['bytes'] for item in catalog
                if item['generation'] not in deleted for shard in item['shards'])
            result['quota_met'] = result['release_bytes_after'] <= max_total_bytes
            return result

    def _clean_trash_locked(self, protected, *, dry_run):
        folder = self.path('gc/trash')
        removed = []
        if not folder.exists():
            return removed
        for index, item in enumerate(folder.iterdir()):
            generation = item.name
            if index >= 1000 or not GENERATION.fullmatch(generation) or generation in protected:
                raise ConceptBlocked('GC_TRASH_NOT_VERIFIED')
            self.path('gc/trash/' + generation)
            marker = self.read_json(f'gc/tombstones/{generation}.json')
            files = marker.get('files', []) if marker else []
            if (not files or any(name != 'manifest.json' and not re.fullmatch(r'shard-\d{2}\.sqlite', name) for name in files)
                    or self._pinned_locked(generation)):
                raise ConceptBlocked('GC_TRASH_NOT_VERIFIED')
            children = list(item.iterdir())
            if not {child.name for child in children} <= set(files):
                raise ConceptBlocked('GC_UNKNOWN_RELEASE_FILES')
            for child in children:
                target = self.path(f'gc/trash/{generation}/{child.name}')
                if not target.is_file():
                    raise ConceptBlocked('GC_UNKNOWN_RELEASE_FILES')
            if dry_run:
                continue
            for child in children:
                self.path(f'gc/trash/{generation}/{child.name}').unlink()
            item.rmdir()
            self.path(f'gc/tombstones/{generation}.json').unlink()
            sync_directory(folder)
            removed.append(generation)
        return removed


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
