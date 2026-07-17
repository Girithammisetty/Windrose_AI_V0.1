"""Version retention policy (DST-FR-080/081) — pure selection logic.

Default policy: keep all versions 90 days; beyond that keep the last
`keep_last` versions plus the latest version of each calendar month for
`monthly_months` months. Never expire the current version or any version
pinned by a `trained` lineage edge younger than `trained_pin_days`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.entities import DatasetVersion


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    keep_all_days: int = 90
    keep_last: int = 10
    monthly_months: int = 13
    trained_pin_days: int = 400


def select_expirable(
    versions: list[DatasetVersion],
    *,
    now: datetime,
    policy: RetentionPolicy,
    current_version_id: str | None,
    pinned_version_ids: set[str],
) -> list[DatasetVersion]:
    live = [v for v in versions if not v.expired]
    if not live:
        return []
    live.sort(key=lambda v: v.version_no)

    keep_all_cutoff = now - timedelta(days=policy.keep_all_days)
    monthly_cutoff = now - timedelta(days=policy.monthly_months * 31)

    keep_last_ids = {v.id for v in live[-policy.keep_last :]} if policy.keep_last > 0 else set()

    # Latest version per calendar month qualifies as a monthly boundary.
    monthly_boundary_ids: set[str] = set()
    by_month: dict[tuple[int, int], DatasetVersion] = {}
    for v in live:
        key = (v.created_at.year, v.created_at.month)
        cur = by_month.get(key)
        if cur is None or v.version_no > cur.version_no:
            by_month[key] = v
    for v in by_month.values():
        if v.created_at >= monthly_cutoff:
            monthly_boundary_ids.add(v.id)

    expirable: list[DatasetVersion] = []
    for v in live:
        if v.id == current_version_id or v.id in pinned_version_ids:
            continue  # DST-FR-081: never expired
        if v.created_at >= keep_all_cutoff:
            continue
        if v.id in keep_last_ids or v.id in monthly_boundary_ids:
            continue
        expirable.append(v)
    return expirable
