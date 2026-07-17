"""Swappable workflow-execution backend registry (Phase 3).

The compute-plane backend is pluggable by NAME (`executor_backend`) via the same
registry pattern the data-plane uses (dataset-service `adapters/registry.py`),
replacing the hardcoded ``if executor_backend == "argo"`` branch. Adding a new
backend (e.g. a Temporal or Kubeflow executor) is a one-line ``register(...)``
with no wiring change.

`local` resolves to ``None`` — training runs inline through
``LocalTrainingExecutor`` with no separate workflow backend; every other name
resolves to a real workflow adapter (infra-gated).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.config import Settings

# Returns a workflow backend, or None for the inline-local path.
type BackendFactory = Callable[[Settings], Any | None]


class WorkflowBackendRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, BackendFactory] = {}

    def register(self, name: str, factory: BackendFactory) -> None:
        self._factories[name] = factory

    def names(self) -> list[str]:
        return sorted(self._factories)

    def create(self, name: str, settings: Settings):
        factory = self._factories.get(name)
        if factory is None:
            raise ValueError(
                f"unknown executor_backend {name!r}; registered: {', '.join(self.names())}"
            )
        return factory(settings)


def _local_backend(_settings: Settings):
    # Inline local training (LocalTrainingExecutor) needs no separate backend.
    return None


def _argo_backend(settings: Settings):
    # Infra-gated: real Argo REST; raises DependencyUnavailable when unreachable.
    from app.executor.argo import ArgoWorkflowExecutor

    return ArgoWorkflowExecutor(settings.argo_server_url)


WORKFLOW_BACKENDS = WorkflowBackendRegistry()
WORKFLOW_BACKENDS.register("local", _local_backend)
WORKFLOW_BACKENDS.register("argo", _argo_backend)


def resolve_workflow_backend(settings: Settings):
    """Select the workflow backend by `settings.executor_backend` (default local)."""
    return WORKFLOW_BACKENDS.create(settings.executor_backend or "local", settings)
