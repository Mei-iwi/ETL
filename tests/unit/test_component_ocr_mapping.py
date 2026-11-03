from contextlib import contextmanager
from types import SimpleNamespace

from data_sync_etl.application.component_ocr_mapping import TrustedComponentMapping
from tests.unit.test_component_ocr_projection import job, page, unit


class FakeUow:
    def __init__(self):
        self.version = {'id': 'master-v', 'resource_id': 'master-r', 'source_hash': 'a' * 64}
        self.resource = {'id': 'master-r', 'source_origin': 'POSTGRESQL', 'source_missing_at': None}
        self.imports = [{'resource_id': 'master-r', 'source_version_id': 'version-1',
                         'source_system': 'registered-source', 'source_hash': 'a' * 64}]

    @contextmanager
    def __call__(self):
        yield self

    def get(self, table, key):
        return self.version if table == 'resource_versions' and key == 'master-v' else self.resource

    def find(self, table, selector, limit):
        return self.imports


def test_trusted_mapping_checks_namespace_version_and_registered_authority():
    uow = FakeUow()
    identity = SimpleNamespace(resource_mapping=lambda _: dict(status='ACTIVE', namespace='source-a',
                                                               source_id='registered-source'))
    resolver = SimpleNamespace(derived_index_authorized=lambda *_, **__: True)
    verify = TrustedComponentMapping(uow, identity, resolver)
    assert verify('job', job())
    assert verify('page', page())
    assert verify('unit', unit())
    assert not verify('job', job(namespace='other'))
    assert not verify('job', job(source_version_id='other'))
    assert not verify('job', job(source_hash='f' * 64))
    resolver.derived_index_authorized = lambda *_, **__: False
    assert not verify('unit', unit())
    uow.resource['source_missing_at'] = 'stale'
    assert not verify('page', page())
