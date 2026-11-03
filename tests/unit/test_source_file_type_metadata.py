from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

import pytest

from data_sync_etl.application.source_file_resolver import SourceFileResolver
from data_sync_etl.domain.core import DomainError, SourceFileError
from data_sync_etl.domain.format_validation import FormatValidatorRegistry


def resolver_for(mime='application/pdf', key='opaque-id'):
    calls = []
    resolver = SourceFileResolver(None, None, None, None, FormatValidatorRegistry(),
                                  SimpleNamespace(source_content_max_bytes=1024 * 1024), None)
    source = {'storage_bucket': 'private', 'storage_object_key': key}
    resolver._canonical_metadata = lambda row: source
    resolver.require_content_capabilities = lambda ref, **kwargs: calls.append('authority')
    def metadata(bucket, object_key):
        assert calls == ['authority']
        calls.append('storage_metadata')
        return {'mime_type': mime, 'file_size': 100}
    resolver.files = SimpleNamespace(file_metadata=metadata)
    version = {'id': 'mv1', 'resource_id': 'r1', 'source_hash': 'a' * 64}
    row = {'storage_bucket': '__SOURCE_METADATA_ONLY__', 'storage_object_key': 'metadata-only',
           'source_version_id': 'v1', 'source_system': 'postgresql'}
    return resolver, version, row, calls


def test_opaque_file_type_comes_from_storage_after_authority_check():
    resolver, version, row, calls = resolver_for()
    ref = resolver._reference(version, row)
    assert calls == ['authority', 'storage_metadata']
    assert ref.filename == 'source.pdf' and ref.mime == 'application/pdf'
    assert ref.opaque_locator == 'opaque-id' and ref.expected_sha256 == 'a' * 64


@pytest.mark.parametrize('mime', ['application/octet-stream', 'text/plain', None])
def test_unknown_or_ambiguous_storage_type_stays_blocked(mime):
    resolver, version, row, _ = resolver_for(mime)
    with pytest.raises(SourceFileError, match='UNSUPPORTED_SOURCE_FORMAT'):
        resolver._reference(version, row)


def test_explicit_unsupported_suffix_never_overridden_by_mime():
    resolver, version, row, calls = resolver_for(key='payload.exe')
    with pytest.raises(SourceFileError, match='UNSUPPORTED_SOURCE_FORMAT'):
        resolver._reference(version, row)
    assert calls == []


def test_opaque_metadata_cannot_be_read_without_capability():
    resolver, version, row, calls = resolver_for()
    def denied(*args, **kwargs):
        raise SourceFileError('SOURCE_CAPABILITY_BLOCKED')
    resolver.require_content_capabilities = denied
    with pytest.raises(SourceFileError, match='SOURCE_CAPABILITY_BLOCKED'):
        resolver._reference(version, row)
    assert calls == []


def test_storage_mime_does_not_bypass_verified_bytes_signature():
    resolver, version, row, _ = resolver_for()
    payload = b'not a PDF despite its storage MIME'
    version['source_hash'] = sha256(payload).hexdigest()
    ref = resolver._reference(version, row)
    stream = BytesIO(payload)
    resolver.files = SimpleNamespace(open_stream=lambda *_args, **_kwargs: stream)
    resolver._recheck = lambda ref: None
    resolver.require_content_capabilities = lambda *_args, **_kwargs: None
    with pytest.raises(DomainError, match='signature'):
        with resolver.open_verified_stream(ref):
            pytest.fail('invalid content escaped signature verification')
    assert stream.closed
