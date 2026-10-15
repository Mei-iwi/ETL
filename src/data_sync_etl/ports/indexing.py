from typing import Protocol

from data_sync_etl.ports.mongo_foundation import (
    ContentReadPort,
    EmbeddingPort,
    LeafKeywordDocumentPort,
)


class IndexContentPort(ContentReadPort, LeafKeywordDocumentPort, Protocol):
    pass


class IndexEmbeddingPort(EmbeddingPort, Protocol):
    def available(self) -> bool: ...
    def prepare(self) -> None: ...
