import io
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

import pytest

from data_sync_etl.domain.core import DomainError


def test_same_bytes_upload_atomically_creates_one_object(container):
    payload = b"file bytes shared by concurrent uploads" * 1000

    def upload(_):
        return container.storage.save_stream(io.BytesIO(payload), suffix=".pdf")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(upload, range(8)))

    digest = sha256(payload).hexdigest()
    assert {item.source_hash for item in results} == {digest}
    assert {item.object_key for item in results} == {f"{digest[:2]}/{digest}.pdf"}
    assert sum(item.created for item in results) == 1
    path = container.storage.get_local_path(results[0].bucket, results[0].object_key)
    assert path.read_bytes() == payload
    assert list((container.storage.root / "resources").glob(".upload-*")) == []


def test_existing_object_is_not_overwritten(container):
    payload = b"original bytes"
    first = container.storage.save_stream(io.BytesIO(payload), suffix=".pdf")
    path = container.storage.get_local_path(first.bucket, first.object_key)
    path.write_bytes(b"unexpected bytes")

    with pytest.raises(DomainError, match="hash mismatch"):
        container.storage.save_stream(io.BytesIO(payload), suffix=".pdf")
    assert path.read_bytes() == b"unexpected bytes"
    assert list((container.storage.root / "resources").glob(".upload-*")) == []


def test_failed_stream_leaves_no_temp_file(container):
    class BrokenStream:
        def __init__(self):
            self.calls = 0

        def read(self, _size):
            self.calls += 1
            if self.calls > 1:
                raise OSError("controlled stream failure")
            return b"partial upload"

    with pytest.raises(OSError, match="controlled stream failure"):
        container.storage.save_stream(BrokenStream(), suffix=".pdf")
    assert list((container.storage.root / "resources").rglob(".upload-*")) == []


def test_storage_rejects_path_escape(container):
    with pytest.raises(DomainError):
        container.storage.get_local_path("resources", "../outside.pdf")
