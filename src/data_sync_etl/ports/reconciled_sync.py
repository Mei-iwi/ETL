from typing import Protocol


class ReconciledSyncPort(Protocol):
    def run(self, stream: str, full: bool = False) -> dict: ...
