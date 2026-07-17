"""Windrose resource URNs (MASTER-FR-013): wr:<tenant>:<service>:<type>/<id>."""

from __future__ import annotations

SERVICE = "agent"


def proposal_urn(tenant_id: str, proposal_id: str) -> str:
    return f"wr:{tenant_id}:{SERVICE}:proposal/{proposal_id}"


def run_urn(tenant_id: str, run_id: str) -> str:
    return f"wr:{tenant_id}:{SERVICE}:run/{run_id}"


def session_urn(tenant_id: str, session_id: str) -> str:
    return f"wr:{tenant_id}:{SERVICE}:session/{session_id}"


def agent_urn(tenant_id: str, agent_key: str, version: int) -> str:
    return f"wr:{tenant_id}:{SERVICE}:agent/{agent_key}@v{version}"


def case_urn(tenant_id: str, case_id: str) -> str:
    return f"wr:{tenant_id}:case:case/{case_id}"


def pipeline_training_urn(tenant_id: str, algorithm: str) -> str:
    """The training-pipeline resource a model-training proposal would create/launch
    (no template id exists until the proposal is approved), keyed by algorithm."""
    return f"wr:{tenant_id}:pipeline:training/{algorithm}"


def dashboard_urn(tenant_id: str, slug: str) -> str:
    """The dashboard resource a dashboard-designer proposal would create (no id
    exists until the proposal is approved), keyed by a title slug. Matches
    chart-service's ``wr:<tenant>:chart:dashboard/<id>`` scheme."""
    return f"wr:{tenant_id}:chart:dashboard/{slug}"


def urn_tenant(urn: str) -> str:
    parts = urn.split(":", 3)
    return parts[1] if len(parts) >= 4 and parts[0] == "wr" else ""
