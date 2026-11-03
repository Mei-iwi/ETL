from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from data_sync_etl.adapters.source import postgresql_ocr_write as module
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.source_ocr_contract import SourceOCRContract


class Result:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def scalars(self):
        return self

    def one_or_none(self):
        return self.value

    def all(self):
        return self.value


class Connection:
    def __init__(self):
        self.job = None
        self.task = None
        self.task_path = None
        self.total_pages = 1
        self.writes = 0
        self.source_hash = 'a' * 64
        self.active = True
        self.lifecycle = 'APPROVED'

    def execute(self, statement, params=None):
        sql = str(statement)
        if 'FROM public.resource_versions' in sql:
            return Result({'id': 'v1', 'source_hash': self.source_hash, 'lifecycle_status': self.lifecycle})
        if 'FROM public.users' in sql:
            return Result({'id': 'u1', 'is_active': self.active})
        if 'FROM public.ocr_jobs' in sql and 'FOR UPDATE' in sql:
            return Result({'id': self.job, 'resource_version_id': 'v1', 'status': 'QUEUED',
                           'total_pages': self.total_pages}) if self.job else Result(None)
        if 'FROM public.ocr_jobs' in sql:
            return Result([self.job] if self.job else [])
        if 'FROM public.ocr_page_tasks' in sql:
            return Result([{'id': self.task, 'local_image_path': self.task_path}] if self.task else [])
        if 'INSERT INTO public.ocr_jobs' in sql:
            self.job = params['id']
            self.writes += 1
        if 'INSERT INTO public.ocr_page_tasks' in sql:
            self.task = params['id']
            self.task_path = params['path']
            self.writes += 1
        if 'UPDATE public.ocr_jobs SET total_pages=' in sql:
            self.total_pages = params['total']
            self.writes += 1
        return Result(None)


class Engine:
    dialect = SimpleNamespace(name='postgresql')

    def __init__(self):
        self.conn = Connection()

    @contextmanager
    def begin(self):
        original = self.conn.job, self.conn.task, self.conn.task_path, self.conn.total_pages, self.conn.writes
        try:
            yield self.conn
        except Exception:
            self.conn.job, self.conn.task, self.conn.task_path, self.conn.total_pages, self.conn.writes = original
            raise


def policy():
    return SourceOCRContract.from_owner_contract({
        'backend': 'postgresql', 'schema': 'public', 'entity_type': 'ocr_processing', 'namespace': 'source-a',
        'tables': ['ocr_jobs', 'ocr_page_tasks'],
        'columns': {'ocr_jobs': list(module._JOB_COLUMNS), 'ocr_page_tasks': list(module._TASK_COLUMNS)},
        'actions': ['select', 'insert', 'update'], 'allowed_roles': ['ADMIN'], 'allowed_actor_ids': ['a1'],
        'allowed_version_ids': ['v1'], 'allowed_lifecycles': ['APPROVED'], 'statement_timeout_seconds': 10,
    }, expected_namespace='source-a')


def request():
    return dict(namespace='source-a', master_version_id='m1', source_version_id='v1', source_hash='a' * 64,
                actor_id='a1', role='ADMIN', source_user_id='u1')


def test_job_creation_is_idempotent_and_requires_trusted_identity(monkeypatch):
    engine = Engine()
    monkeypatch.setattr(module, 'PostgreSQLSourceIDCounter', lambda _: object())
    monkeypatch.setattr(module, 'SourceIDService', lambda _: SimpleNamespace(next_id=lambda *_args, **_kw: 'OCR1'))
    writer = module.PostgreSQLSourceOCRWriter(engine, policy(), authorize_identity=lambda _: True)
    assert writer.ensure_job(**request()) == 'OCR1'
    assert writer.ensure_job(**request()) == 'OCR1'
    assert engine.conn.writes == 1
    denied = module.PostgreSQLSourceOCRWriter(engine, policy(), authorize_identity=lambda _: False)
    with pytest.raises(DomainError, match='SOURCE_OCR_UNAUTHORIZED'):
        denied.ensure_job(**request())


def test_reauthorization_failure_rolls_back_job(monkeypatch):
    engine = Engine()
    monkeypatch.setattr(module, 'PostgreSQLSourceIDCounter', lambda _: object())
    monkeypatch.setattr(module, 'SourceIDService', lambda _: SimpleNamespace(next_id=lambda *_args, **_kw: 'OCR1'))
    grants = iter([True, True, False])
    writer = module.PostgreSQLSourceOCRWriter(engine, policy(), authorize_identity=lambda _: next(grants))
    with pytest.raises(DomainError, match='SOURCE_OCR_UNAUTHORIZED'):
        writer.ensure_job(**request())
    assert engine.conn.job is None
    assert engine.conn.writes == 0


@pytest.mark.parametrize(('attribute', 'value', 'error'), [
    ('source_hash', 'b' * 64, 'SOURCE_VERSION_MUTATED'),
    ('active', False, 'SOURCE_OCR_ACTOR_INVALID'),
    ('lifecycle', 'REJECTED', 'SOURCE_OCR_CONTRACT_DENIED'),
])
def test_stale_inactive_or_out_of_scope_source_never_allocates(attribute, value, error):
    engine = Engine()
    setattr(engine.conn, attribute, value)
    writer = module.PostgreSQLSourceOCRWriter(engine, policy(), authorize_identity=lambda _: True)
    with pytest.raises(DomainError, match=error):
        writer.ensure_job(**request())
    assert engine.conn.writes == 0


def test_page_task_requires_real_staging_and_is_idempotent(tmp_path, monkeypatch):
    engine = Engine()
    engine.conn.job = 'OCR1'
    image = tmp_path / 'page.png'
    image.write_bytes(b'fixture')
    monkeypatch.setattr(module, 'PostgreSQLSourceIDCounter', lambda _: object())
    monkeypatch.setattr(module, 'SourceIDService', lambda _: SimpleNamespace(next_id=lambda *_args, **_kw: 'OPT1'))
    writer = module.PostgreSQLSourceOCRWriter(engine, policy(), authorize_identity=lambda _: True)
    kwargs = {**request(), 'source_job_id': 'OCR1', 'page_num': 1, 'staging_path': image, 'staging_root': tmp_path}
    assert writer.ensure_page_task(**kwargs) == 'OPT1'
    assert writer.ensure_page_task(**kwargs) == 'OPT1'
    assert engine.conn.writes == 1
    other_image = tmp_path / 'other.png'
    other_image.write_bytes(b'other')
    with pytest.raises(DomainError, match='SOURCE_OCR_STAGING_CONFLICT'):
        writer.ensure_page_task(**{**kwargs, 'staging_path': other_image})
    with pytest.raises(DomainError, match='SOURCE_OCR_STAGING_INVALID'):
        writer.ensure_page_task(**{**kwargs, 'staging_path': tmp_path / 'missing.png'})


def test_page_count_is_set_once_and_never_changed_on_conflict():
    engine = Engine()
    engine.conn.job, engine.conn.total_pages = 'OCR1', 0
    writer = module.PostgreSQLSourceOCRWriter(engine, policy(), authorize_identity=lambda _: True)
    kwargs = {**request(), 'source_job_id': 'OCR1', 'total_pages': 2}
    writer.set_page_count(**kwargs)
    writer.set_page_count(**kwargs)
    assert engine.conn.total_pages == 2
    assert engine.conn.writes == 1
    with pytest.raises(DomainError, match='SOURCE_OCR_PAGE_COUNT_CONFLICT'):
        writer.set_page_count(**{**kwargs, 'total_pages': 3})
    assert engine.conn.total_pages == 2
