"""TTL bounds, half-lives, confidence defaults, ISO-8601 duration parsing.

Implements MEM-FR-003 (per-scope TTL bounds, tenant-tunable), BR-16 (recency
half-lives), MEM-FR-013 (confidence defaults) and AC-13 (bound enforcement).
"""

from __future__ import annotations

import re
from datetime import timedelta

from app.domain.entities import (
    SCOPE_TENANT,
    SCOPE_USER,
    SCOPE_WORKSPACE,
    SRC_AGENT_RUN,
    SRC_TOOL_OUTPUT,
    SRC_USER_EXPLICIT,
)
from app.domain.errors import ValidationFailed

_ISO_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def parse_iso_duration(value: str) -> timedelta:
    m = _ISO_DUR.match(value or "")
    if not m or value == "P":
        raise ValidationFailed(f"invalid ISO-8601 duration: {value!r}")
    days, hours, minutes, seconds = (int(x) if x else 0 for x in m.groups())
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def default_ttl_days(scope: str, settings) -> int:
    return {
        SCOPE_USER: settings.ttl_user_default_days,
        SCOPE_WORKSPACE: settings.ttl_workspace_default_days,
        SCOPE_TENANT: settings.ttl_tenant_default_days,
    }[scope]


def max_ttl_days(scope: str, settings) -> int | None:
    # Only the user scope has an explicit platform max in the BRD (400d).
    return settings.ttl_user_max_days if scope == SCOPE_USER else None


def resolve_ttl(scope: str, settings, policy_overrides: dict | None) -> timedelta:
    """Resolve the TTL for a scope, applying tenant override within bounds."""
    days = default_ttl_days(scope, settings)
    ttl = timedelta(days=days)
    overrides = policy_overrides or {}
    if scope in overrides:
        ttl = parse_iso_duration(overrides[scope])
    cap = max_ttl_days(scope, settings)
    if cap is not None and ttl > timedelta(days=cap):
        raise ValidationFailed(
            f"ttl for scope {scope} exceeds platform bound of {cap} days"
        )
    return ttl


def validate_ttl_override(scope: str, value: str, settings) -> None:
    ttl = parse_iso_duration(value)
    cap = max_ttl_days(scope, settings)
    if cap is not None and ttl > timedelta(days=cap):
        raise ValidationFailed(
            f"ttl override for scope {scope} exceeds platform bound of {cap} days"
        )


def half_life_seconds(scope: str, settings) -> float:
    days = {
        SCOPE_USER: settings.half_life_user_days,
        SCOPE_WORKSPACE: settings.half_life_workspace_days,
        SCOPE_TENANT: settings.half_life_tenant_days,
    }.get(scope, settings.half_life_user_days)
    return days * 86400.0


def default_confidence(source_type: str, settings) -> float:
    return {
        SRC_USER_EXPLICIT: settings.conf_user_explicit,
        SRC_AGENT_RUN: settings.conf_agent_run,
        SRC_TOOL_OUTPUT: settings.conf_tool_output,
    }.get(source_type, settings.conf_agent_run)


def scope_cap(scope: str, settings) -> int | None:
    return {
        SCOPE_USER: settings.cap_user,
        SCOPE_WORKSPACE: settings.cap_workspace,
        SCOPE_TENANT: settings.cap_tenant,
    }.get(scope)
