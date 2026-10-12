from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from data_sync_etl.application.source_ids import SourceIDService
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.source_ids import SOURCE_PREFIXES, validate_counter_key


@pytest.mark.parametrize("prefix", sorted(SOURCE_PREFIXES))
def test_source_prefix_format(prefix):
    counter = Mock()
    counter.next_sequence.return_value = 1
    assert (
        SourceIDService(counter).next_id(prefix, created_at=datetime(2026, 6, 1, tzinfo=UTC))
        == f"{prefix}062600001"
    )
    counter.next_sequence.assert_called_once_with(prefix, "0626")


def test_utc_month_and_explicit_creation_time():
    counter = Mock()
    counter.next_sequence.return_value = 99999
    value = datetime(2026, 7, 1, 1, tzinfo=timezone(timedelta(hours=7)))
    assert SourceIDService(counter).next_id("RES", created_at=value) == "RES062699999"
    with pytest.raises(DomainError):
        SourceIDService(counter).next_id("RES", created_at=datetime(2026, 6, 1))


@pytest.mark.parametrize(
    "prefix,period",
    [
        ("BAD", "0626"),
        ("res", "0626"),
        ("RES", "0026"),
        ("RES", "1326"),
        ("RES", "626"),
        ("RES", "06ab"),
        ("RES", "0626x"),
    ],
)
def test_invalid_counter_key(prefix, period):
    with pytest.raises(DomainError):
        validate_counter_key(prefix, period)


@pytest.mark.parametrize("value", [0, 100000])
def test_invalid_allocator_result(value):
    counter = Mock()
    counter.next_sequence.return_value = value
    with pytest.raises(DomainError):
        SourceIDService(counter).next_id("RES", created_at=datetime(2026, 6, 1, tzinfo=UTC))
