"""Register all graph modules on import (side-effectful registration)."""

from app.graphs import (  # noqa: F401
    analytics,
    dashboard_designer,
    governance,
    inference_agent,
    meta_router,
    model_training,
    onboarding,
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
    "inference": ("inference.v1", inference_agent.run_inference),
    "meta-router": ("meta_router.v1", meta_router.run_meta_router),
}

__all__ = ["GraphDeps", "GraphOutcome", "WriteIntent", "register", "get_graph_module",
           "graph_digest", "RUNNERS"]
