"""Centralized, versioned agent prompt registry.

Every agent's SYSTEM prompt lives as a plain-text ``.md`` file in this package —
one reviewable source of truth, editable without touching graph code. This module
loads a prompt by name, computes a content digest (the immutability ref that
``AgentVersion.prompt_refs`` records — see app/agents/catalog.py), and exposes
typed accessors. The prompt TEXT is authoritative here; graphs import it via
``system_prompt(...)`` rather than hardcoding a string literal.

Adding / changing a prompt:
  * edit (or add) ``<id>.md`` in this directory,
  * register its id + semantic version in ``VERSIONS`` (bump the version on a
    material edit — the sha256 digest recomputes automatically and flags drift),
  * for a NEW agent, also map its catalog key -> prompt id in
    ``AGENT_SYSTEM_PROMPT`` so the catalog wires a real digest.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import cache
from importlib.resources import files

# prompt id -> semantic version. Bump on a material edit to the .md; the content
# digest is derived automatically and is the real drift signal.
VERSIONS: dict[str, int] = {
    "triage.system": 2,
    "analytics.system": 1,
    "governance.system": 1,
    "persona_copilot.system": 2,
    "dashboard_designer.system": 1,
    "inference.system": 1,
    "meta_router.system": 1,
    "onboarding.system": 1,
    "model_training.system": 1,
    "ml_engineer.system": 1,
}

# agent catalog key -> its system-prompt id (drives catalog prompt_refs wiring).
# persona_copilot is the shared custom-agent base graph, not a catalog entry, so
# it is intentionally absent here.
AGENT_SYSTEM_PROMPT: dict[str, str] = {
    "case-triage": "triage.system",
    "governance": "governance.system",
    "analytics": "analytics.system",
    "onboarding": "onboarding.system",
    "dashboard-designer": "dashboard_designer.system",
    "model-training": "model_training.system",
    "ml-engineer": "ml_engineer.system",
    "inference": "inference.system",
    "meta-router": "meta_router.system",
}


@dataclass(frozen=True, slots=True)
class Prompt:
    id: str
    version: int
    text: str
    digest: str  # "sha256:<hex[:32]>"

    @property
    def ref(self) -> dict:
        """The ``AgentVersion.prompt_refs`` entry for this prompt."""
        return {"id": self.id, "version": self.version, "digest": self.digest}


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


@cache
def get(name: str) -> Prompt:
    """Load a prompt by id (e.g. ``"triage.system"``). Cached; the .md's single
    trailing newline (added by the authoring tool) is stripped so the text is the
    exact prompt the model receives."""
    try:
        raw = files(__name__).joinpath(f"{name}.md").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, IsADirectoryError) as exc:
        raise KeyError(f"unknown prompt {name!r}") from exc
    text = raw.rstrip("\n")
    return Prompt(id=name, version=VERSIONS.get(name, 1), text=text, digest=_digest(text))


def system_prompt(name: str) -> str:
    """The system-prompt text for ``name`` (what graphs put in the system role)."""
    return get(name).text


def all_ids() -> list[str]:
    return sorted(VERSIONS)
