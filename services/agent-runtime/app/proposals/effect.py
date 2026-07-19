"""Server-derived, tamper-proof approval effect — anti "description laundering".

Red-teaming (Microsoft AI Red Team, 2026) found human-in-the-loop approval is the
single most-exploited control: a compromised or manipulated agent launders a
dangerous action behind a benign-sounding, model-authored description so the
approver waves it through. The defence is to make the approver decide on the
GROUND TRUTH of what will execute — never on prose the model can craft.

``derive_effect`` computes a canonical effect from the intent's *structured* fields
only (tool, tier, side-effects, the exact args, the affected URNs), plus a risk
tier that scales the human-oversight requirement by the two properties that matter
most: blast-radius (how many resources) and reversibility. The model's own
``summary`` is preserved but demoted to ``agent_summary`` (stated, unverified).

Nothing here trusts a number the model produced: ``blast_radius`` is recomputed
from the affected-URN count, so a model cannot understate blast to dodge tiering.
"""

from __future__ import annotations

from app.domain.canonical import args_digest

# At/above this many affected resources a write is "high" risk even when each
# individual change is reversible — a bulk mutation is its own blast-radius class.
HIGH_BLAST_THRESHOLD = 25

# Tiers that are inherently high-risk regardless of side-effects/blast.
_HIGH_TIERS = ("write-direct", "admin")


def _reversibility(side_effects: str) -> str:
    """Map the tool's declared side-effect class to a plain reversibility word.
    Unknown/empty is treated as irreversible (fail-safe: assume the worst)."""
    return {
        "none": "none",
        "reversible": "reversible",
        "destructive": "irreversible",
    }.get(side_effects or "", "irreversible")


def derive_effect(
    *, tool_id: str, tier: str, side_effects: str, args: dict,
    affected_urns: list[str] | None, model_effect: dict | None = None,
) -> dict:
    """Return the authoritative ``predicted_effect`` for a proposal.

    Ground-truth fields (``blast_radius``, ``reversibility``, ``risk``,
    ``authoritative_summary``, ``args_digest``) are computed here and override
    anything the graph/model supplied; the model's own ``summary`` is kept as
    ``agent_summary`` so the approver can still read the agent's reasoning,
    clearly marked as unverified.
    """
    urns = list(affected_urns or [])
    blast = len(urns)
    reversibility = _reversibility(side_effects)
    risk = "high" if (
        reversibility == "irreversible"
        or blast >= HIGH_BLAST_THRESHOLD
        or tier in _HIGH_TIERS
    ) else "low"

    noun = "resource" if blast == 1 else "resources"
    summary = f"Runs {tool_id} (tier {tier}, {reversibility}); affects {blast} {noun}."

    out = dict(model_effect or {})
    if out.get("summary"):
        out["agent_summary"] = out["summary"]  # demote model prose to "stated"
    out.update({
        "blast_radius": blast,               # ground-truth, overrides model's number
        "reversibility": reversibility,
        "risk": risk,
        "authoritative_summary": summary,
        "args_digest": args_digest(args or {}),
    })
    return out
