from data_sync_etl.application.capabilities import CapabilityService


def test_production_capabilities_describe_configuration_without_secrets():
    service = CapabilityService(
        chunk_size_words=200,
        chunk_overlap_words=40,
        vector_dimension=256,
        ocr_engine='tesseract',
        source_adapter='postgresql',
        metadata_backend='postgresql',
        mongodb_enabled=True,
        auto_sync_supported=True,
    )
    data = service.describe()
    assert data['master_sync']['source'] == 'postgresql'
    assert data['master_sync']['auto_sync_supported'] is True
    assert data['processing']['scanned_pdf_ocr'] is True
    assert data['processing']['fake_ocr_demo'] is False
    assert data['storage']['content_and_index'] == 'mongodb'
    assert data['storage']['mongodb_enabled'] is True
    assert data['representations']['vector']['method'] is None
    assert data['representations']['vector']['configured'] is False
    assert data['representations']['vector']['runtime_verified'] is False
    assert 'subject_id' in data['retrieval']['filters']


def test_native_engine_does_not_claim_scanned_ocr_support():
    data = CapabilityService(200, 40, 256, 'native').describe()
    assert data['processing']['scanned_pdf_ocr'] is False
    assert data['processing']['normalized_content'] is True


def test_available_local_model_is_not_inferred_or_claimed_verified_by_http():
    class Unprobed:
        model_id, dimension = 'local-test-only', 384

        def available(self):
            return True

        def ready(self):
            return False

    data = CapabilityService(200, 40, 256, 'native', embedding_runtime=Unprobed()).describe()
    assert data['representations']['vector']['runtime_status'] == 'NOT_VERIFIED'
    assert data['representations']['vector']['configured'] is True
    assert data['representations']['vector']['runtime_verified'] is False
    assert data['representations']['vector']['runtime_scope'] == 'current_process'
    assert not data['representations']['vector']['enabled']
    assert data['representations']['vector']['dimensions'] == 384
    assert data['representations']['representation_unit'] == 'content_bag'


def test_search_capabilities_match_api_contract_without_runtime():
    from typing import get_args

    from data_sync_etl.api.search_schemas import Method, SearchFilters

    retrieval = CapabilityService(200, 40, 384, 'native').describe()['retrieval']
    assert retrieval['modes'] == list(get_args(Method))
    assert retrieval['filters'] == list(SearchFilters.model_fields)
    assert retrieval['methods'][0] == {
        'mode': 'bow', 'requires_embedding': False, 'runtime_status': 'NOT_REQUIRED',
    }
    assert all(method['runtime_status'] == 'BLOCKED_EMBEDDING_RUNTIME'
               for method in retrieval['methods'][1:])
    assert retrieval['context_supported'] is True
    assert retrieval['pagination_supported'] is True
    assert retrieval['public_search_supported'] is False
    assert retrieval['access_policy'] == 'LOCAL_DEMO_ONLY'


def test_search_runtime_readiness_is_reported_without_loading_or_inference():
    class Runtime:
        model_id, dimension = 'test-only', 384
        verified = False

        def available(self):
            return True

        def ready(self):
            return self.verified

        def prepare(self):
            raise AssertionError('Capabilities must not load a model')

        def embed(self, *_):
            raise AssertionError('Capabilities must not run inference')

    runtime = Runtime()
    service = CapabilityService(200, 40, 384, 'native', embedding_runtime=runtime)
    for verified, expected in ((False, 'NOT_VERIFIED'), (True, 'READY')):
        runtime.verified = verified
        data = service.describe()
        assert data['representations']['vector']['dimensions'] == 384
        assert data['representations']['vector']['configured'] is True
        assert data['representations']['vector']['runtime_verified'] is verified
        assert data['retrieval']['methods'][0]['runtime_status'] == 'NOT_REQUIRED'
        assert all(method['runtime_status'] == expected for method in data['retrieval']['methods'][1:])
