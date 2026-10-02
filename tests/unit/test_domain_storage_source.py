import hashlib
import io
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from data_sync_etl.adapters.storage.local import BLOCK_SIZE, stream_hash
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError, job_terminal, new_id, normalize_text
from data_sync_etl.ports.source import Cursor


@pytest.mark.parametrize(
    "text,expected",
    [
        ("  hello  world \r\n\r\n next  ", "hello world\n\nnext"),
        ("a\n\n\n\nb", "a\n\nb"),
        ("e\u0301", "é"),
        ("hy-\nphen", "hy-\nphen"),
        (" \t\n", ""),
    ],
)
def test_normalize(text, expected):
    assert normalize_text(text) == expected


@pytest.mark.parametrize(
    "values,status",
    [((1, 0, 3), "RUNNING"), ((3, 0, 3), "COMPLETED"), ((2, 1, 3), "COMPLETED_WITH_ERRORS")],
)
def test_status(values, status):
    assert job_terminal(*values) == status


def test_ids():
    ids = {new_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(len(value) == 26 for value in ids)


def test_config():
    assert Settings(_env_file=None).sync_batch_size == 100
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ocr_max_retries=0)
    assert "secret" not in repr(Settings(_env_file=None, database_url="secret"))


@pytest.mark.parametrize(
    "bucket,key",
    [
        ("../outside", "file"),
        ("bucket", "../../file"),
        ("bucket", "/absolute"),
        ("bucket", "C:/private"),
        ("bucket", r"..\file"),
        ("bucket", "file:stream"),
        ("/absolute", "a"),
    ],
)
def test_storage_traversal(container, bucket, key):
    with pytest.raises(DomainError):
        container.storage.get_local_path(bucket, key)


def test_stream_hash_bounded():
    class BoundedStream(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= BLOCK_SIZE
            return super().read(size)

    value = b"x" * (BLOCK_SIZE * 2 + 10)
    assert stream_hash(BoundedStream(value)) == hashlib.sha256(value).hexdigest()


def test_source_pagination_and_cursor(container):
    batches = list(container.source.iter_learning_resources(batch_size=2))
    assert [len(batch) for batch in batches] == [2, 1]
    cursor = batches[0][0].cursor
    assert [
        a.cursor.id for b in container.source.iter_learning_resources(cursor, 1) for a in b
    ] == ["resource-2", "resource-3"]
    assert cursor.updated_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert Cursor.model_validate_json(cursor.model_dump_json()) == cursor


def test_soft_deleted_source_readable(container):
    path = container.source.root / "resources.json"
    data = json.loads(path.read_text())
    data[0].update(is_active=False, deleted_at="2026-01-02T00:00:00Z")
    path.write_text(json.dumps(data))
    assert next(container.source.batches("resources", None, 1))[0].values["is_active"] is False


def test_password_source_repr(container):
    user = next(container.source.records("users"))
    assert "DEMO_NON_AUTHENTICATING_HASH" not in repr(user)
