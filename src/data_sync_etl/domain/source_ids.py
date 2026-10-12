import re
from datetime import UTC, datetime

from data_sync_etl.domain.core import DomainError

SOURCE_PREFIXES = frozenset(
    {"ROL", "USR", "EDU", "GRD", "SUB", "PRV", "RTY", "RES", "RSV", "CUN", "OCR", "OPT"}
)


def validate_counter_key(prefix: str, period: str) -> None:
    if (
        not isinstance(prefix, str)
        or not isinstance(period, str)
        or prefix not in SOURCE_PREFIXES
        or not re.fullmatch(r"(?:0[1-9]|1[0-2])[0-9]{2}", period)
    ):
        raise DomainError("Invalid source ID prefix or MMYY period")


def source_period(created_at: datetime) -> str:
    if (
        not isinstance(created_at, datetime)
        or created_at.tzinfo is None
        or created_at.utcoffset() is None
    ):
        raise DomainError("Source ID requires an explicit timezone-aware creation time")
    return created_at.astimezone(UTC).strftime("%m%y")
