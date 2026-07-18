"""SLM distillation archetypes (design doc §archetypes).

An *archetype* is a cluster of transcripts that share a task shape — in the
built pipeline the cluster key IS the ``agent_key`` (milestone 2 curates one SFT
dataset per agent_key). Each vertical pack's agent personas are natural
archetype seeds. This module resolves an archetype to its training defaults: the
small OPEN student base to fine-tune, and the ladder-rung alias a promoted
adapter would serve as. Pure config — no IO, unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass

# The small open-weight students an SLM can be distilled onto. An allowlist so a
# training submission can't point at an arbitrary/huge/closed base. Extend as
# the GPU trainer backend gains support.
KNOWN_BASE_MODELS = (
    "meta-llama/Llama-3.2-1B-Instruct",
    "meta-llama/Llama-3.2-3B-Instruct",
    "Qwen/Qwen2.5-1.5B-Instruct",
    "Qwen/Qwen2.5-3B-Instruct",
    "microsoft/Phi-3.5-mini-instruct",
    "mistralai/Ministral-3B-Instruct",
)
DEFAULT_BASE_MODEL = "meta-llama/Llama-3.2-3B-Instruct"


@dataclass(slots=True)
class Archetype:
    """A resolved distillation target for one archetype (agent_key)."""

    key: str  # the agent_key the SLM specializes on
    base_model: str  # the open student base to LoRA-fine-tune
    model_alias: str  # the ai-gateway ladder-rung alias a promoted adapter serves as


def model_alias_for(archetype: str) -> str:
    """The rung alias a promoted adapter for this archetype serves as — the
    cheapest bottom rung of the tenant's ladder (design §M4)."""
    return f"slm-{archetype}"


def resolve_archetype(agent_key: str, *, base_model: str | None = None) -> Archetype:
    """Resolve training defaults for an archetype. ``base_model`` overrides the
    default but must be one of KNOWN_BASE_MODELS (fail closed on a bogus base)."""
    if not agent_key or not agent_key.strip():
        raise ValueError("archetype (agent_key) is required")
    base = base_model or DEFAULT_BASE_MODEL
    if base not in KNOWN_BASE_MODELS:
        raise ValueError(
            f"base_model {base!r} is not a supported student "
            f"(one of {', '.join(KNOWN_BASE_MODELS)})"
        )
    return Archetype(key=agent_key.strip(), base_model=base, model_alias=model_alias_for(agent_key.strip()))  # noqa: E501
