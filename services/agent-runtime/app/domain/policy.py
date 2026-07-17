"""Auto-execute policy matrix + version resolution (ART-FR-042/043, BR-1/BR-3).

Defense-in-depth for **destructive-never-auto**: this is the runtime evaluator
(layer 2); the config API validation (layer 1, ``validate_auto_policy``) and
tool-plane (layer 3) are the other two.
"""

from __future__ import annotations

from app.domain.errors import ValidationFailed

HARD_MANUAL_SIDE_EFFECTS = ("destructive",)
HARD_MANUAL_TIERS = ("admin",)


def validate_auto_policy(policy: dict) -> None:
    """Layer 1 (ART-FR-043): the API rejects any attempt to set a destructive
    cell or an admin-tier cell to ``auto`` (422). ``*`` wildcards included."""
    for agent_key, tiers in (policy or {}).items():
        if not isinstance(tiers, dict):
            raise ValidationFailed(f"auto_execute_policy[{agent_key}] must be an object")
        for tier, cells in tiers.items():
            if tier in HARD_MANUAL_TIERS and _has_auto(cells):
                raise ValidationFailed(
                    f"admin-tier cells can never be auto (agent {agent_key})")
            if isinstance(cells, dict):
                for side_effect, mode in cells.items():
                    if side_effect in HARD_MANUAL_SIDE_EFFECTS and mode == "auto":
                        raise ValidationFailed(
                            f"destructive cells can never be auto (agent {agent_key})")


def _has_auto(cells) -> bool:
    if isinstance(cells, dict):
        return any(v == "auto" for v in cells.values())
    return cells == "auto"


def is_auto_execute(policy: dict, agent_key: str, tier: str, side_effects: str) -> bool:
    """Layer 2: resolve {agent_key × tier × side_effects} → auto|manual, honouring
    ``*`` wildcards. Hard rules override any stored config: destructive and admin
    are ALWAYS manual regardless of what the row says."""
    if side_effects in HARD_MANUAL_SIDE_EFFECTS or tier in HARD_MANUAL_TIERS:
        return False
    for a_key in (agent_key, "*"):
        agent_cells = (policy or {}).get(a_key)
        if not isinstance(agent_cells, dict):
            continue
        for t in (tier, "*"):
            cell = agent_cells.get(t)
            if isinstance(cell, dict):
                for se in (side_effects, "*"):
                    if se in cell:
                        return cell[se] == "auto"
            elif isinstance(cell, str):
                return cell == "auto"
    return False  # default: everything manual


def resolve_version(
    *,
    kill_active: bool,
    pinned_version: int | None,
    canary_version: int | None,
    default_version: int,
) -> int:
    """Version resolution order (BR-3): kill (refuse, handled by caller) > tenant
    pin > active canary assignment > cell default. ``kill_active`` True means the
    caller must refuse (AGENT_KILLED) before reaching here."""
    if pinned_version is not None:
        return pinned_version
    if canary_version is not None:
        return canary_version
    return default_version


def canary_assignment(session_seed: str, pct: int) -> bool:
    """Deterministic-by-session-hash canary assignment (ART-FR-061, AC-7)."""
    import hashlib

    h = int(hashlib.sha256(session_seed.encode()).hexdigest(), 16) % 100
    return h < max(0, min(100, pct))
