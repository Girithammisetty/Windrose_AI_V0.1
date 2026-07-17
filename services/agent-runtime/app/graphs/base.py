"""Graph registry + shared deps. The runtime is agent-agnostic (ART-FR-040):
adding an agent = registering a graph module id, no runtime fork.

A graph is a factory ``build(deps) -> compiled LangGraph`` plus a declared
``graph_ref``/``graph_digest``. Execution returns a ``GraphOutcome``: either a
final assistant answer or a WRITE INTENT that the runtime converts into a
Proposal (never a direct write).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GraphDeps:
    llm: Any
    memory: Any = None
    case_reader: Any = None
    ingestion_reader: Any = None
    experiment_reader: Any = None
    dataset_reader: Any = None
    pipeline_reader: Any = None
    semantic_reader: Any = None
    catalog_reader: Any = None
    prompt_params: dict = field(default_factory=dict)
    obo_token: str | None = None
    # Replay / no-side-effect mode (ART-FR-015): when True the run reproduces what
    # the agent WOULD have done — write tools are captured-not-executed and memory
    # writes are suppressed. ``memory_snapshot_ver`` pins RAG reads to a specific
    # corpus snapshot so grounding is deterministic (reproduces retrieval as of the
    # snapshot, not live memory). None = live retrieval (normal runs).
    replay: bool = False
    memory_snapshot_ver: str | None = None


@dataclass(slots=True)
class WriteIntent:
    tool_id: str
    tool_version: str
    tier: str
    side_effects: str
    args: dict
    rationale: str
    affected_urns: list[str]
    predicted_effect: dict
    # The rbac action the underlying write requires (e.g. "case.case.update").
    # The runtime enforces it against the INVOKING caller before creating or
    # auto-executing the proposal (permission-aware, on-behalf-of): the copilot
    # never proposes/executes an action the caller could not perform themselves
    # (ART-FR-044 caller-gate). None = declare no action (legacy / autonomous).
    required_action: str | None = None


@dataclass(slots=True)
class GraphOutcome:
    final_text: str | None = None
    write_intent: WriteIntent | None = None
    usage: dict = field(default_factory=dict)
    trace: list[dict] = field(default_factory=list)
    # Structured disposition + grounding evidence surfaced for replay/eval scoring
    # (ART-FR-015). ``evidence`` are the retrieved memories the answer was grounded
    # in (the groundedness judge scores the answer against these). Empty for agents
    # that produce neither (e.g. analytics defaults).
    structured: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)


_REGISTRY: dict[str, Callable[[], Any]] = {}


def register(graph_ref: str):
    def deco(fn):
        _REGISTRY[graph_ref] = fn
        return fn
    return deco


def get_graph_module(graph_ref: str):
    if graph_ref not in _REGISTRY:
        raise KeyError(f"unknown graph_ref {graph_ref!r}")
    return _REGISTRY[graph_ref]


def graph_digest(graph_ref: str) -> str:
    """Content digest of the registered graph module source (immutability ref)."""
    import inspect

    src = inspect.getsource(get_graph_module(graph_ref))
    return "sha256:" + hashlib.sha256(src.encode()).hexdigest()[:32]
