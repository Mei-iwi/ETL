from types import SimpleNamespace

from data_sync_etl.application.processing_state import intake_status, projection_fingerprint
from data_sync_etl.application.source_ocr_inventory import SourceOCRInventory
from tests.unit.test_source_ocr_inventory import audit
from tests.unit.test_source_ocr_namespace_scope import enable_proposed_scope
from tests.unit.test_source_ocr_runtime import runtime as runtime


def test_ready_work_survives_restart_and_cursor_moving_past_it(runtime, monkeypatch):
    r = runtime
    assert audit(r) == ['mv1']
    r.uow.update('sync_checkpoints', r.pipeline.inventory.stream, {'last_id': 'zzz'})
    scope = r.pipeline.inventory._scope
    r.pipeline.inventory = SourceOCRInventory(r.pipeline)
    monkeypatch.setattr(r.pipeline.inventory, '_scope', scope)
    monkeypatch.setattr(r.pipeline.inventory, 'once', lambda **_: (_ for _ in ()).throw(AssertionError('full scan')))
    assert r.pipeline.schedule_authorized_source_job()
    assert len(r.source.jobs) == 1
    assert r.uow.get('sync_checkpoints', r.pipeline.inventory.stream)['last_id'] == 'zzz'
    assert r.uow.get('resource_versions', 'mv1')['processing_options']['_source_ocr_audit']['status'] == 'QUEUED'


def test_new_metadata_does_not_wait_for_historical_cursor(runtime):
    r = runtime
    r.uow.ensure_checkpoint(r.pipeline.inventory.stream)
    r.uow.update('sync_checkpoints', r.pipeline.inventory.stream, {'last_id': 'zzz'})
    assert r.pipeline.inventory.once(limit=1, force=True, prioritize_new=True) == ['mv1']
    assert r.uow.get('sync_checkpoints', r.pipeline.inventory.stream)['last_id'] == 'zzz'


def test_invalid_ready_hint_is_removed_so_other_versions_can_progress(runtime, monkeypatch):
    r = runtime
    assert audit(r) == ['mv1']
    monkeypatch.setattr(r.pipeline, 'scheduling_row', lambda _: None)
    assert not r.pipeline.schedule_authorized_source_job()
    assert r.pipeline.inventory.ready() == []
    assert r.uow.get('resource_versions','mv1')['processing_options']['_source_ocr_audit']['status'] == 'RECHECK_REQUIRED'


def test_queued_page_is_claimed_before_metadata_inventory(runtime, monkeypatch):
    r = runtime
    r.pipeline.create_job('mv1', access=r.access)
    monkeypatch.setattr(r.pipeline, 'schedule_authorized_source_job',
                        lambda: (_ for _ in ()).throw(AssertionError('audit delayed OCR page')))
    assert r.container.worker.once('fast-page')
    assert len(r.content.pages) == 1


def test_page_claims_rotate_between_jobs_instead_of_starving_short_documents(runtime, monkeypatch):
    r = runtime
    states = [('mv1',{'identity':{'namespace':'source-a'},'source_job_id':'j1','scheduled':True}),
              ('mv2',{'identity':{'namespace':'source-a'},'source_job_id':'j2','scheduled':True})]
    monkeypatch.setattr(r.pipeline,'states',lambda **_:states)
    monkeypatch.setattr(r.pipeline,'state',lambda v:dict(states)[v])
    monkeypatch.setattr(r.pipeline,'sync',lambda _:None)
    monkeypatch.setattr(r.pipeline.queue,'claim',lambda _identity,j,*a,**k:{'source_job_id':j})
    assert r.pipeline.claim('fair')['source_job_id'] == 'j1'
    assert r.pipeline.claim('fair')['source_job_id'] == 'j2'


def test_continuous_pages_still_give_metadata_inventory_a_bounded_turn(runtime, monkeypatch):
    r = runtime
    r.pipeline.create_job('mv1',access=r.access)
    calls = []
    monkeypatch.setattr(r.pipeline,'schedule_authorized_source_job',lambda:calls.append('inventory') or False)
    r.container.worker._tasks_since_inventory = 5
    assert r.container.worker.once('fair-inventory')
    assert calls == ['inventory'] and len(r.content.pages) == 1


def test_intake_states_do_not_describe_synced_versions_as_unprocessed():
    version = {'processing_options': {'source_version_identity': {'namespace': 'source'}}}
    assert intake_status(version) == 'RECEIVED'
    for status, expected in [('CHECKING','CHECKING_OCR'), ('QUEUED','OCR_QUEUED'),
                             ('WAITING_FOR_PROCESS_AUTHORIZATION','WAITING_AUTHORIZATION'),
                             ('UNVERIFIED','PROVENANCE_REQUIRED')]:
        version['processing_options']['_source_ocr_audit'] = {'status':status}
        assert intake_status(version) == expected
    # A delayed audit cannot overwrite actual running job progress.
    assert intake_status(version, {'status':'RUNNING'}) == 'OCR_RUNNING'


def test_authority_reuses_only_this_calls_fresh_reference_and_rechecks_next_call(runtime, monkeypatch):
    r = runtime
    enable_proposed_scope(r)
    r.pipeline.create_job('mv1', access=r.access)
    reference = r.pipeline.resolver.resolve('mv1')
    adapter = r.pipeline.resolver._source_adapter('SRC_TEST')
    original = adapter.get_versions
    reads = []

    def fresh(_version):
        reads.append('source')
        return SimpleNamespace(**(vars(reference) | {'source_snapshot': original(['v1'])['v1']}))

    monkeypatch.setattr(r.pipeline.resolver, 'resolve', fresh)
    monkeypatch.setattr(adapter, 'get_versions', lambda _: (_ for _ in ()).throw(AssertionError('duplicate read')))
    identity = r.pipeline.state('mv1')['identity']
    assert r.pipeline.authorize_identity(identity)
    r.source.versions['v1']['lifecycle_status'] = 'FAILED'
    assert not r.pipeline.authorize_identity(identity)
    assert reads == ['source', 'source']


def test_index_state_does_not_claim_a_job_that_was_never_enqueued():
    job = {'id':'j','status':'COMPLETED','completed_pages':1,'total_pages':1,'failed_pages':0}
    post = {'stage':'POSTPROCESS','status':'COMPLETED','input_fingerprint':'p'}
    stages = [post,{'stage':'MONGO_PROJECTION','status':'COMPLETED',
                   'input_fingerprint':projection_fingerprint('j',post)}]
    assert intake_status({},job,stages) == 'CONTENT_READY'
    assert intake_status({},job,[{'stage':'INDEX_BUILD','status':'PENDING'},*stages]) == 'INDEXING'
    assert intake_status({},job,[{'stage':'INDEX_BUILD','status':'COMPLETED'},*stages]) == 'READY'
    version = {'processing_options':{'_source_ocr_audit':{'status':'UNVERIFIED'}}}
    assert intake_status(version,job,stages) == 'PROVENANCE_REQUIRED'
    version['processing_options']['_source_ocr_audit']['status'] = 'WAITING_FOR_PROCESS_AUTHORIZATION'
    assert intake_status(version,job,stages) == 'WAITING_AUTHORIZATION'
