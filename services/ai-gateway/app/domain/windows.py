"""Budget window math (BR-4): daily = tenant-local midnight, monthly = 1st of
month tenant-local; boundaries are computed server-side only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


def _tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 - unknown tz falls back to UTC
        return ZoneInfo("UTC")


def window_start(window: str, now: datetime, tz_name: str = "UTC") -> str:
    """ISO date of the current window's start in the tenant's timezone."""
    local = now.astimezone(_tz(tz_name))
    if window == "monthly":
        return local.date().replace(day=1).isoformat()
    return local.date().isoformat()


def window_reset_at(window: str, now: datetime, tz_name: str = "UTC") -> datetime:
    """UTC instant at which the current window resets."""
    tz = _tz(tz_name)
    local = now.astimezone(tz)
    if window == "monthly":
        if local.month == 12:
            nxt = local.replace(year=local.year + 1, month=1, day=1)
        else:
            nxt = local.replace(month=local.month + 1, day=1)
        nxt = nxt.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        nxt = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nxt.astimezone(UTC)
