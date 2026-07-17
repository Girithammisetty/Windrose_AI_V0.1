"""Register all graph modules on import (side-effectful registration)."""

from app.graphs import (  # noqa: F401
    analytics,
    dashboard_designer,
    governance,
    inference_agent,
    meta_router,
    ml_engineer,
    model_training,
    onboarding,
    persona_copilot,
    triage,
)
from app.graphs.base import (
    GraphDeps,
    GraphOutcome,
    WriteIntent,
    get_graph_module,
    graph_digest,
    register,
)

# Agent key -> run_* entrypoint (used by the run engine / activities).
RUNNERS = {
    "case-triage": ("triage.v1", triage.run_triage),
    "governance": ("governance.v1", governance.run_governance),
    "analytics": ("analytics.v1", analytics.run_analytics),
    "onboarding": ("onboarding.v1", onboarding.run_onboarding),
    "dashboard-designer": ("dashboard_designer.v1",
                           dashboard_designer.run_dashboard_designer),
    "model-training": ("model_training.v1", model_training.run_model_training),
    "ml-engineer": ("ml_engineer.v1", ml_engineer.run_ml_engineer),
    # BRD 53: the shared graph tenant CUSTOM agents run on. Custom agents get a
    # distinct agent_key but all resolve to this same run function; RUNNERS maps
    # by agent_key, so the engine dispatches custom keys here via a fallback (see
    # engine.run_graph). The persona_copilot key itself is a usable template too.
    "persona-copilot": ("persona_copilot.v1", persona_copilot.run_persona_copilot),
    "inference": ("inference.v1", inference_agent.run_inference),
    "meta-router": ("meta_router.v1", meta_router.run_meta_router),
}

# graph_ref -> run_* entrypoint. Tenant CUSTOM agents (BRD 53) have their own
# agent_key (not in RUNNERS) but all resolve to the shared persona_copilot.v1
# graph — the engine falls back to this map by the agent version's graph_ref.
# ONLY graphs safe for tenant config-driven use belong here.
GRAPH_RUNNERS = {
    "persona_copilot.v1": persona_copilot.run_persona_copilot,
}

__all__ = ["GraphDeps", "GraphOutcome", "WriteIntent", "register", "get_graph_module",
           "graph_digest", "RUNNERS", "GRAPH_RUNNERS"]
