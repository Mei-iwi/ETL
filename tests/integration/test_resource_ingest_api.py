import io
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier

import pymupdf
import pytest
from fastapi.testclient import TestClient

from data_sync_etl.application.create_resource import CreateResourceUseCase
from data_sync_etl.domain.core import ResourceCreateError
from data_sync_etl.domain.format_validation import FormatValidatorRegistry
from data_sync_etl.domain.resource import CreateResourceCommand
from data_sync_etl.main import create_app


def pdf_bytes():
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "PostgreSQL upload integration")
        return pdf.tobytes()


def command(payload, title):
    return CreateResourceCommand(
        title=title,
        resource_type_code="HANDOUT",
        provider_id="integration-provider",
        provider_name="Integration provider",
        filename="sample.pdf",
        file_stream=io.BytesIO(payload),
        file_size=len(payload),
        declared_mime="application/pdf",
    )


@pytest.mark.postgres
def test_concurrent_upload_and_process_api_on_postgres(pg_container):
    c = pg_container
    c.settings.admin_enabled = True
    payload = pdf_bytes()

    def post(number):
        with TestClient(create_app(c), client=("127.0.0.1", 50000)) as api:
            return api.post(
                "/api/v1/resources",
                data={
                    "title": f"Concurrent resource {number}",
                    "resource_type_code": "HANDOUT",
                    "provider_id": "integration-provider",
                    "provider_name": "Integration provider",
                },
                files={"file": ("sample.pdf", io.BytesIO(payload), "application/pdf")},
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(post, range(2)))
    assert [response.status_code for response in responses] == [201, 201]
    versions = [response.json()["initial_version_id"] for response in responses]
    with c.uow() as repo:
        stored_versions = [repo.get("resource_versions", version) for version in versions]
        assert all(version is not None for version in stored_versions)
        assert len({version["storage_object_key"] for version in stored_versions}) == 1
        assert repo.count("master_learning_resources", {}) == 2
    with TestClient(create_app(c), client=("127.0.0.1", 50000)) as api:
        assert api.post(f"/api/v1/resource-versions/{versions[0]}/process").status_code == 202
    assert c.storage.exists(
        stored_versions[0]["storage_bucket"], stored_versions[0]["storage_object_key"]
    )


@pytest.mark.postgres
def test_shared_upload_survives_concurrent_database_rollback(pg_container):
    c = pg_container
    payload = pdf_bytes()
    barrier = Barrier(2)

    class CoordinatedUow:
        def __init__(self, fail):
            self.fail = fail
            self.calls = 0

        def __call__(self):
            self.calls += 1
            if self.calls == 1:
                return c.uow()

            @contextmanager
            def transaction():
                with c.uow() as repo:
                    barrier.wait(timeout=10)
                    if self.fail:

                        class FailingRepo:
                            def __getattr__(self, name):
                                return getattr(repo, name)

                            def insert(self, table, values):
                                if table == "resource_versions":
                                    raise RuntimeError("controlled version insert failure")
                                return repo.insert(table, values)

                        yield FailingRepo()
                    else:
                        yield repo

            return transaction()

    good = CreateResourceUseCase(CoordinatedUow(False), c.storage, FormatValidatorRegistry())
    bad = CreateResourceUseCase(CoordinatedUow(True), c.storage, FormatValidatorRegistry())
    with ThreadPoolExecutor(max_workers=2) as pool:
        success = pool.submit(good.execute, command(payload, "Committed resource"))
        failure = pool.submit(bad.execute, command(payload, "Rolled back resource"))
        committed = success.result(timeout=15)
        with pytest.raises(ResourceCreateError) as error:
            failure.result(timeout=15)
    assert error.value.status_code == 503
    with c.uow() as repo:
        assert repo.count("master_learning_resources", {}) == 1
        version = repo.get("resource_versions", committed.initial_version_id)
        assert version is not None
    assert c.storage.exists(version["storage_bucket"], version["storage_object_key"])
    assert list((c.storage.root / "resources").glob(".upload-*")) == []
