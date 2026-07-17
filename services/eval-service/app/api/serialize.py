"""Entity -> API dict serialization."""

from __future__ import annotations

import dataclasses
from datetime import datetime


def _v(x):
    if isinstance(x, datetime):
        return x.isoformat()
    return x


def dump(entity) -> dict:
    if entity is None:
        return None
    out = {}
    for f in dataclasses.fields(entity):
        if f.name.startswith("_"):
            continue
        out[f.name] = _v(getattr(entity, f.name))
    return out


def dump_page(page) -> dict:
    return {
        "data": [dump(i) for i in page.items],
        "next_cursor": page.next_cursor,
        "has_more": page.has_more,
    }
