"""Calendar audit slots in local time; comparisons and API timestamps use UTC."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = 'Asia/Ho_Chi_Minh'
INTERVALS = (60, 300, 600, 900, 1200, 1800, 3600)


def suggested_schedule(stream, branch):
    return dict(enabled=False, cadence='DAILY' if branch == 'fast' else
                'MONTHLY' if stream == 'files' else 'WEEKLY', hour=2 if branch == 'fast' else 3,
                minute=0, weekday=6, month_day=1, timezone=DEFAULT_TIMEZONE,
                interval_seconds=1800, repair=False)


def validate_schedule(plan):
    if plan['cadence'] not in ('INTERVAL', 'DAILY', 'WEEKLY', 'MONTHLY'):
        raise ValueError('INVALID_AUDIT_SCHEDULE')
    for key, low, high in [('hour', 0, 23), ('minute', 0, 59), ('weekday', 0, 6), ('month_day', 1, 28)]:
        if type(plan[key]) is not int or not low <= plan[key] <= high:
            raise ValueError('INVALID_AUDIT_SCHEDULE')
    if plan['interval_seconds'] not in INTERVALS or type(plan['enabled']) is not bool or type(plan['repair']) is not bool:
        raise ValueError('INVALID_AUDIT_SCHEDULE')
    try:
        ZoneInfo(plan['timezone'])
    except (KeyError, ValueError) as exc:
        raise ValueError('INVALID_AUDIT_TIMEZONE') from exc


def schedule_slot(plan, stamp):
    """Return only the current period's due slot, never catch up old periods.

    DST folds use the first occurrence; a nonexistent wall time rolls forward
    across the gap by the timezone's UTC conversion. One period has one key.
    """
    stamp = stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
    zone = ZoneInfo(plan['timezone'])
    local = stamp.astimezone(zone)
    cadence = plan['cadence']
    if cadence == 'INTERVAL':
        seconds = plan['interval_seconds']
        slot = int(stamp.timestamp() // seconds)
        return f'{seconds}:{slot}', datetime.fromtimestamp((slot + 1) * seconds, UTC)
    day = local.date()
    if cadence == 'WEEKLY':
        day = day - timedelta(days=day.weekday()) + timedelta(days=plan['weekday'])
        period = (local.date() - timedelta(days=local.weekday())).isoformat()
    elif cadence == 'MONTHLY':
        day = day.replace(day=plan['month_day'])
        period = local.strftime('%Y-%m')
    else:
        period = day.isoformat()
    occurrence = datetime(day.year, day.month, day.day, plan['hour'], plan['minute'], tzinfo=zone)
    due_at = occurrence.astimezone(UTC)
    if stamp < due_at:
        return None, due_at
    if cadence == 'MONTHLY':
        following = occurrence.replace(year=day.year + (day.month == 12), month=day.month % 12 + 1)
    else:
        following = occurrence + timedelta(days=7 if cadence == 'WEEKLY' else 1)
    return period, following.astimezone(UTC)
