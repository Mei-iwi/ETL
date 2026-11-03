from types import SimpleNamespace
from unittest.mock import MagicMock

import pymupdf
import pytest

from data_sync_etl.application.ocr import OCRPipeline
from data_sync_etl.domain.core import DomainError
from data_sync_etl.ports.processing import OCRResult


def test_successful_empty_scan_keeps_engine_evidence_for_source_reuse():
    engine = SimpleNamespace(recognize_bytes=MagicMock(
        return_value=OCRResult('', engine_name='tesseract', engine_version='fixture-engine')))
    pipeline = OCRPipeline(None, None, None, engine)
    with pymupdf.open() as document:
        document.new_page()
        payload = document.tobytes()
    with pytest.raises(DomainError, match='EMPTY_EXTRACTED_CONTENT'):
        pipeline._recognize_bytes({'page_num': 1}, {}, 'blank.pdf', payload)
    result = pipeline._recognize_bytes({'page_num': 1}, {}, 'blank.pdf', payload, allow_empty=True)
    assert result.text == ''
    assert (result.engine_name, result.engine_version) == ('tesseract', 'fixture-engine')
    assert result.metadata['ocr_empty'] is True
    assert engine.recognize_bytes.call_count == 2


def test_source_empty_policy_never_turns_an_engine_failure_into_empty_success():
    engine = SimpleNamespace(recognize_bytes=MagicMock(side_effect=DomainError('OCR_RUNTIME_FAILED')))
    pipeline = OCRPipeline(None, None, None, engine)
    with pymupdf.open() as document:
        document.new_page()
        payload = document.tobytes()
    with pytest.raises(DomainError, match='OCR_RUNTIME_FAILED'):
        pipeline._recognize_bytes({'page_num': 1}, {}, 'blank.pdf', payload, allow_empty=True)
