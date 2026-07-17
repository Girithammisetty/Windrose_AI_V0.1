"""Relative time-range resolution (BR-5).

Resolved at compile time in the tenant's reporting timezone (workspace setting,
default UTC); the absolute bounds are returned in provenance and bound as
parameters — never inlined.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.errors import ValidationFailed

_LAST_N = re.compile(r"^last_(\d{1,3})_(day|week|month|year)s?$")


def _month_add(d: date, months: int) -> date:
    zero_based = d.year * 12 + (d.month - 1) + months
    return date(zero_based // 12, zero_based % 12 + 1, 1)


def resolve_relative(relative: str, now: datetime, timezone: str) -> tuple[date, date]:
    """Return [start, end) date bounds for a relative range keyword."""
    try:
        today = now.astimezone(ZoneInfo(timezone)).date()
    except Exception as exc:  # noqa: BLE001 - bad tz configuration
        raise ValidationFailed(f"unknown reporting timezone {timezone!r}") from exc

    m = _LAST_N.match(relative)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if n < 1:
            raise ValidationFailed("relative range must cover at least one period")
        if unit == "day":
            return today - timedelta(days=n), today
        if unit == "week":
            week_start = today - timedelta(days=today.weekday())
            return week_start - timedelta(weeks=n), week_start
        if unit == "month":
            month_start = today.replace(day=1)
            return _month_add(month_start, -n), month_start
        year_start = today.replace(month=1, day=1)
        return year_start.replace(year=year_start.year - n), year_start

    tomorrow = today + timedelta(days=1)
    if relative == "today":
        return today, tomorrow
    if relative == "yesterday":
        return today - timedelta(days=1), today
    if relative == "this_week":
        return today - timedelta(days=today.weekday()), tomorrow
    if relative == "this_month":
        return today.replace(day=1), tomorrow
    if relative == "this_quarter":
        quarter_month = 3 * ((today.month - 1) // 3) + 1
        return today.replace(month=quarter_month, day=1), tomorrow
    if relative == "this_year":
        return today.replace(month=1, day=1), tomorrow
    raise ValidationFailed(
        f"unknown relative range {relative!r}; use last_N_days|weeks|months|years, "
        "today, yesterday, this_week, this_month, this_quarter, this_year")
