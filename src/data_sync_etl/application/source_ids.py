from datetime import datetime

from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.source_ids import source_period, validate_counter_key
from data_sync_etl.ports.source_ids import SourceIDCounter


class SourceIDService:
    """Explicit SOURCE-only allocation; the caller owns the CREATE transaction."""

    def __init__(self, counter: SourceIDCounter):
        self.counter = counter

    def next_id(self, prefix: str, *, created_at: datetime) -> str:
        period = source_period(created_at)
        validate_counter_key(prefix, period)
        sequence = self.counter.next_sequence(prefix, period)
        if not 1 <= sequence <= 99999:
            raise DomainError("Source ID sequence exhausted")
        return f"{prefix}{period}{sequence:05d}"
