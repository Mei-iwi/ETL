from typing import Protocol


class SourceIDCounter(Protocol):
    def next_sequence(self, prefix: str, period: str) -> int: ...
