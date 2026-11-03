"""Conservative OCR reuse decision for a single authorized generation.

This pure decision helper is not yet wired to the production OCR worker.
The caller must obtain data through an authorized component adapter.
"""
import hashlib
import re
from dataclasses import dataclass

_SHA = re.compile(r'^[0-9a-f]{64}$')


@dataclass(frozen=True)
class OCRProbe:
    status: str
    missing_pages: tuple[int, ...] = ()


def probe_ocr(*, authorized, available, namespace, version_id, generation,
              expected_hash, expected_fingerprint, manifest, pages):
    if not authorized:
        return OCRProbe('UNAUTHORIZED')
    if not available:
        return OCRProbe('INACCESSIBLE')
    if (not isinstance(namespace, str) or not namespace.strip()
            or not isinstance(version_id, str) or not version_id.strip()
            or type(generation) is not int or generation < 1):
        return OCRProbe('INVALID_IDENTITY')
    if not isinstance(expected_hash, str) or not _SHA.fullmatch(expected_hash):
        return OCRProbe('INVALID_IDENTITY')
    if not isinstance(expected_fingerprint, str) or not _SHA.fullmatch(expected_fingerprint):
        return OCRProbe('INVALID_IDENTITY')
    if manifest is None:
        return OCRProbe('ABSENT')
    if (manifest.get('source_hash') != expected_hash
            or manifest.get('fingerprint') != expected_fingerprint
            or manifest.get('namespace') != namespace
            or manifest.get('version_id') != version_id
            or manifest.get('generation') != generation):
        return OCRProbe('STALE')
    total = manifest.get('total_pages')
    if type(total) is not int or total < 1 or not manifest.get('verified'):
        return OCRProbe('NOT_READY')
    if not isinstance(pages, dict):
        return OCRProbe('INACCESSIBLE')
    missing = []
    for number in range(1, total + 1):
        page = pages.get(number)
        if not isinstance(page, dict):
            missing.append(number)
            continue
        text = page.get('raw_text')
        checksum = page.get('checksum')
        if (page.get('verified') is not True or not isinstance(text, str)
                or not isinstance(checksum, str) or not _SHA.fullmatch(checksum)
                or page.get('namespace') != namespace or page.get('version_id') != version_id
                or page.get('generation') != generation or page.get('page_num') != number
                or page.get('source_hash') != expected_hash
                or page.get('fingerprint') != expected_fingerprint
                or hashlib.sha256(text.encode('utf-8')).hexdigest() != checksum):
            missing.append(number)
    if missing:
        return OCRProbe('PARTIAL', tuple(missing))
    return OCRProbe('COMPLETE_VERIFIED')
