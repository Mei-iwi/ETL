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
    assert data['retrieval']['filters'] == []


def test_native_engine_does_not_claim_scanned_ocr_support():
    data = CapabilityService(200, 40, 256, 'native').describe()
    assert data['processing']['scanned_pdf_ocr'] is False
    assert data['processing']['normalized_content'] is True
