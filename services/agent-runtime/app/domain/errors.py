"""Domain errors mapped to the MASTER-FR-024 envelope (via py-common web helper)."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status = 500
    code = "INTERNAL"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFound(AppError):
    status = 404
    code = "NOT_FOUND"


class CrossTenantDenied(NotFound):
    # Existence non-leak: cross-tenant access returns a 404 shape (BR-11, AC-14).
    code = "NOT_FOUND"


class Conflict(AppError):
    status = 409
    code = "CONFLICT"


class ProposalDecided(Conflict):
    code = "CONFLICT"


class SessionExpired(AppError):
    status = 409
    code = "SESSION_EXPIRED"


class AgentKilled(AppError):
    status = 423
    code = "AGENT_KILLED"


class ProposalExpired(AppError):
    status = 410
    code = "PROPOSAL_EXPIRED"


class ValidationFailed(AppError):
    status = 422
    code = "VALIDATION_FAILED"


class EvalGateFailed(AppError):
    status = 422
    code = "EVAL_GATE_FAILED"


class PermissionDenied(AppError):
    status = 403
    code = "PERMISSION_DENIED"


class Unauthorized(AppError):
    status = 401
    code = "UNAUTHENTICATED"


class GuardrailViolation(AppError):
    """An agent tried to act outside its declared guardrail envelope (a tool not
    on its allow-list, or a tier above its ceiling). Fail closed — no proposal
    is created — and audited (BRD 53 PA-FR-030/BR-5)."""
    status = 403
    code = "GUARDRAIL_VIOLATION"


class BudgetExhausted(AppError):
    status = 402
    code = "BUDGET_EXHAUSTED"


class OverCapacity(AppError):
    status = 429
    code = "OVER_CAPACITY"
