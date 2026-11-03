"""Transaction and storage doubles for SOURCE wiring tests. Never execute DDL."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

from data_sync_etl.domain.core import now
from data_sync_etl.domain.mongo_documents import ContentUnitDocument, OCRPageDocument


class Result:
    def __init__(self, rows=None):
        self.rows = rows or []

    def mappings(self):
        return self

    def scalars(self):
        return self

    def scalar(self):
        return next(iter(self.rows[0].values())) if self.rows else None

    scalar_one = scalar

    def one_or_none(self):
        return self.rows[0] if self.rows else None

    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]

    def all(self):
        return self.rows


class SourceEngine:
    dialect = SimpleNamespace(name='postgresql')

    def __init__(self, checksum):
        self.versions = {'v1': dict(id='v1', source_hash=checksum, lifecycle_status='APPROVED', updated_at=now())}
        self.jobs, self.tasks, self.units = {}, {}, {}
        self.sql = []

    @contextmanager
    def begin(self):
        original = deepcopy((self.jobs, self.tasks, self.units))
        try:
            yield self
        except Exception:
            self.jobs, self.tasks, self.units = original
            raise

    def connect(self):
        return self.begin()

    def execute(self, statement, params=None):
        sql, p = str(statement), params or {}
        self.sql.append(sql)
        assert not any(word in sql.upper() for word in ('CREATE ', 'ALTER ', 'DROP ', 'TRUNCATE '))
        if sql == 'SELECT current_database()':
            return Result([{'database': 'source'}])
        if sql.startswith(('SET TRANSACTION', 'SELECT set_config')):
            return Result()
        if sql.startswith('SELECT pg_try_advisory'):
            return Result([{'locked': True}])
        if sql.startswith('SELECT count(*) FROM public.ocr_page_tasks'):
            return Result([{'count': sum(row['status'] == 'PROCESSING' for row in self.tasks.values())}])
        if 'FROM public.users' in sql:
            return Result([{'is_active': True}] if sql.startswith('SELECT is_active') else [{'id': 'u1', 'is_active': True}])
        if 'FROM public.resource_versions' in sql:
            if sql.startswith('SELECT lifecycle_status'):
                return Result([{'lifecycle_status': self.versions[p['id']]['lifecycle_status']}])
            return Result([dict(self.versions[p['id']])])
        if 'FROM public.ocr_jobs' in sql:
            if 'resource_version_id=:id' in sql or 'resource_version_id = :id' in sql:
                rows = [dict(row) for row in self.jobs.values() if row['resource_version_id'] == p['id']]
                if sql.startswith('SELECT id FROM'):
                    return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row['id'] for row in rows]))
                return Result(rows)
            return Result([dict(self.jobs[p['id']])])
        if 'FROM public.ocr_page_tasks' in sql:
            rows = [dict(row) for row in self.tasks.values()]
            if 'WHERE id=:id' in sql:
                rows = [row for row in rows if row['id'] == p['id']]
            else:
                rows = [row for row in rows if row['job_id'] == p.get('job', p.get('id'))]
            if 'page_num=:page' in sql:
                rows = [row for row in rows if row['page_num'] == p['page']]
            if "status='PENDING'" in sql:
                rows = [row for row in rows if row['status'] == 'PENDING'][:1]
            if "status='PROCESSING'" in sql:
                rows = [row for row in rows if row['status'] == 'PROCESSING'
                        and (row['heartbeat_at'] or row['updated_at']) < p['cutoff']]
            return Result(sorted(rows, key=lambda row: row['page_num']))
        if 'FROM public.content_units' in sql:
            rows = [dict(row) for row in self.units.values()]
            if 'mongo_id=:mongo' in sql:
                rows = [row for row in rows if row['resource_version_id'] == p['version'] and row['mongo_id'] == p['mongo']]
            elif 'WHERE id=:id' in sql:
                rows = [row for row in rows if row['id'] == p['id']]
            else:
                rows = [row for row in rows if row['resource_version_id'] == p['id']]
            return Result(rows)
        if sql.startswith('INSERT INTO public.ocr_jobs'):
            self.jobs[p['id']] = dict(id=p['id'], resource_version_id=p['version'], triggered_by=p['actor'],
                total_pages=0, completed_pages=0, failed_pages=0, status='QUEUED', started_at=None,
                completed_at=None, created_at=p['created'], updated_at=p['created'])
        elif sql.startswith('INSERT INTO public.ocr_page_tasks'):
            self.tasks[p['id']] = dict(id=p['id'], job_id=p['job'], page_num=p['page'], local_image_path=p['path'],
                status='PENDING', retry_count=0, worker_id=None, heartbeat_at=None, started_at=None,
                completed_at=None, error_message=None, created_at=p['created'], updated_at=p['created'])
        elif sql.startswith('INSERT INTO public.content_units'):
            self.units[p['id']] = dict(p, created_at=p['stamp'], updated_at=p['stamp'])
        elif sql.startswith('UPDATE public.ocr_jobs SET total_pages'):
            self.jobs[p['id']].update(total_pages=p['total'], updated_at=p['updated'])
        elif sql.startswith('UPDATE public.ocr_jobs SET completed_pages'):
            self.jobs[p['id']].update(completed_pages=p['ok'], failed_pages=p['failed'], status=p['status'],
                                      completed_at=p['finished'], updated_at=p['stamp'])
        elif sql.startswith('UPDATE public.ocr_jobs'):
            self.jobs[p['id']].update(status='PROCESSING', started_at=p['stamp'], updated_at=p['stamp'])
        elif sql.startswith('UPDATE public.ocr_page_tasks'):
            rows = [self.tasks[p['id']]] if 'WHERE id=:id' in sql else [row for row in self.tasks.values()
                if row['job_id'] == p['id'] and row['status'] == 'FAILED']
            for row in rows:
                if 'owner' in p:
                    row.update(status='PROCESSING', worker_id=p['owner'], started_at=p['stamp'], heartbeat_at=p['stamp'])
                elif 'retries' in p:
                    row.update(status=p['status'], retry_count=p['retries'], completed_at=p.get('finished'),
                               error_message=p.get('error'), heartbeat_at=p['stamp'])
                elif "status='PENDING'" in sql:
                    row.update(status='PENDING', worker_id=None, completed_at=None)
                    if 'retry_count=0' in sql:
                        row['retry_count'] = 0
                else:
                    row['heartbeat_at'] = p['stamp']
                row['updated_at'] = p['stamp']
        else:
            raise AssertionError(f'Unsupported test statement: {sql}')
        return Result()

    def dispose(self):
        pass


class MemoryUow:
    def __init__(self):
        self.tables, self.serial = {}, 0

    def source_index_authorities(self, version_ids):
        result = {}
        for version_id in version_ids:
            version = self.get('resource_versions', version_id)
            if version:
                result[version_id] = (version, self.get('master_learning_resources', version['resource_id']),
                                      self.find('source_file_imports', {'target_version_id': version_id}))
        return result

    def ensure_checkpoint(self, stream):
        if not self.get('sync_checkpoints', stream):
            self.insert('sync_checkpoints', {'id': stream, 'stream_name': stream, 'last_id': None})

    def source_ocr_authority(self, version_id, actor_id):
        version = self.get('resource_versions', version_id)
        user = self.get('master_users', actor_id.removeprefix('master:'))
        resource = self.get('master_learning_resources', version['resource_id']) if version else None
        if not version or not user or not resource:
            return None
        return version, resource, user, self.find('source_file_imports', {'target_version_id':version_id}, limit=2)

    def source_ocr_states(self, namespace, *, active_only=False):
        result = []
        for version in self.source_ocr_inventory_page(namespace, limit=10001):
            state = (version.get('processing_options') or {}).get('_source_ocr')
            jobs = self.find('ocr_jobs', {'resource_version_id': version['id']})
            if (state and state.get('source_job_id') and
                    (not active_only or not jobs or jobs[0]['status'] not in ('COMPLETED', 'FAILED', 'CANCELLED'))):
                result.append((version['id'], state))
        return result

    def source_ocr_ready_versions(self, namespace, *, resource_ids=None, limit=10):
        return [v['id'] for v in self.source_ocr_inventory_page(namespace, resource_ids=resource_ids, limit=10001)
                if (v.get('processing_options') or {}).get('_source_ocr_audit', {}).get('needs_execution')
                and v['processing_options']['_source_ocr_audit'].get('execution_authorized')][:limit]

    def source_ocr_inventory_page(self, namespace, *, after=None, resource_ids=None, limit=10, unaudited_only=False):
        return [version for version in self.find('resource_versions', order=('id',))
                if (not after or version['id'] > after)
                and (not unaudited_only or not (version.get('processing_options') or {}).get('_source_ocr_audit')
                     or version['processing_options']['_source_ocr_audit'].get('status') == 'RECHECK_REQUIRED')
                and self.get('master_learning_resources', version['resource_id']).get('source_namespace') == namespace
                and (resource_ids is None or version['resource_id'] in resource_ids)][:limit]

    @contextmanager
    def __call__(self):
        before = deepcopy(self.tables)
        try:
            yield self
        except Exception:
            self.tables = before
            raise

    @contextmanager
    def processing_lock(self, _version):
        yield True

    def get(self, table, key, **_kwargs):
        return deepcopy(self.tables.get(table, {}).get(key))

    def find(self, table, filters=None, *, order=(), limit=None, **_kwargs):
        rows = [deepcopy(row) for row in self.tables.get(table, {}).values()
                if all(row.get(key) == value for key, value in (filters or {}).items())]
        for field in reversed(order):
            rows.sort(key=lambda row: row.get(field.lstrip('-')) or '', reverse=field.startswith('-'))
        return rows[:limit] if limit is not None else rows

    def insert(self, table, values):
        assert table not in ('ocr_page_results', 'content_unit_texts'), 'Raw MASTER write attempted'
        self.serial += 1
        stamp = now()
        value = {'id': f'm{self.serial}', 'created_at': stamp, 'updated_at': stamp, **values}
        if table == 'content_units':
            value.setdefault('mongo_id', None)
        assert value['id'] not in self.tables.setdefault(table, {})
        self.tables[table][value['id']] = value
        return deepcopy(value)

    def update(self, table, key, values):
        assert table not in ('ocr_page_results', 'content_unit_texts'), 'Raw MASTER write attempted'
        self.tables[table][key].update(deepcopy(values))

    def count(self, table, filters):
        return len(self.find(table, filters))

    def reconciliation_engine(self, **_kwargs):
        return SimpleNamespace(uow=self)

    def file_reconciliation_jobs(self, _engine):
        return SimpleNamespace(close=lambda: None)


class Content:
    def __init__(self):
        self.pages, self.units = {}, {}

    def has_ocr_pages(self, version_id):
        return any(doc.resource_version_id == version_id for doc in self.pages.values())

    def insert_source_ocr_page(self, value):
        page = OCRPageDocument.model_validate(value)
        old = self.pages.get(page.id)
        if old:
            assert old.raw_text == page.raw_text
        else:
            self.pages[page.id] = page

    def ocr_page(self, *, version_id, job_id, page_num):
        return self.pages.get(f'{job_id}:{page_num}')

    def ocr_pages(self, *, version_id, job_id, offset=0, limit=100):
        rows = sorted([doc for doc in self.pages.values() if doc.resource_version_id == version_id
                       and (job_id is None or doc.job_id == job_id)], key=lambda row: row.page_num)
        return rows[offset:offset + limit]

    def get_unit(self, unit_id, *, version_id):
        return self.units.get(unit_id)

    def upsert_content_unit(self, value):
        unit = ContentUnitDocument.model_validate(value)
        self.units[unit.id] = unit
