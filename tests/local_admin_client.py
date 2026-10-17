"""Explicit local-launch attestation for isolated legacy admin contract tests.

Security tests use raw TestClient and exercise every untrusted boundary.
This helper never changes application settings/admin flags or production code.
"""
from contextlib import ExitStack
from unittest.mock import patch

from fastapi.testclient import TestClient as BaseClient


class TestClient(BaseClient):
    __test__ = False

    def __init__(self, app, **kwargs):
        kwargs.setdefault("base_url", "http://127.0.0.1")
        kwargs.setdefault("client", ("127.0.0.1", 50000))
        super().__init__(app, **kwargs)

    def request(self, *args, **kwargs):
        with ExitStack() as attestation:
            attestation.enter_context(patch("sys.argv", [
                "uvicorn/__main__.py", "app", "--host", "127.0.0.1", "--no-proxy-headers"]))
            attestation.enter_context(patch("data_sync_etl.api.search_access.container_runtime", return_value=False))
            return super().request(*args, **kwargs)
