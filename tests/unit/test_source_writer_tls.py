from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from data_sync_etl.composition import _source_writer_database_url
from data_sync_etl.domain.core import DomainError


def test_neon_source_writer_inherits_strict_tls_from_reader(tmp_path):
    ca_file = tmp_path / "neon-ca.pem"
    ca_file.write_text("test certificate", encoding="utf-8")
    settings = SimpleNamespace(
        source_write_database_url_file=None,
        source_write_database_url=(
            "postgresql+psycopg://writer:secret@ep-writer.neon.tech/Learning_Resources"
            "?sslmode=require"
        ),
        source_database_url=(
            "postgresql+psycopg://reader:secret@ep-reader.neon.tech/Learning_Resources"
            f"?sslmode=verify-full&channel_binding=require&sslrootcert={ca_file}"
        ),
    )

    resolved = make_url(_source_writer_database_url(settings))

    assert resolved.host == "ep-writer.neon.tech"
    assert resolved.database == "Learning_Resources"
    assert resolved.query["sslmode"] == "verify-full"
    assert resolved.query["channel_binding"] == "require"
    assert resolved.query["sslrootcert"] == str(ca_file)


def test_neon_source_writer_rejects_non_neon_target():
    settings = SimpleNamespace(
        source_write_database_url_file=None,
        source_write_database_url="postgresql+psycopg://writer:secret@localhost/Learning_Resources",
        source_database_url=(
            "postgresql+psycopg://reader:secret@ep-reader.neon.tech/Learning_Resources"
            "?sslmode=verify-full&channel_binding=require&sslrootcert=C:/ca.pem"
        ),
    )

    with pytest.raises(DomainError, match="SOURCE_WRITE_TARGET_MISMATCH"):
        _source_writer_database_url(settings)
