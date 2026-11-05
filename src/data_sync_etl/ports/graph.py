"""Graph ports accept and return IDs only; no authority or content lives here."""

from typing import Protocol

from data_sync_etl.domain.graph_contract import GraphSnapshot


class GraphWritePort(Protocol):
    def read(self) -> GraphSnapshot: ...

    def ensure_schema(self) -> None: ...


class GraphReadPort(Protocol):
    def resolve(self, candidate_ids: list[str]) -> list[dict]: ...
