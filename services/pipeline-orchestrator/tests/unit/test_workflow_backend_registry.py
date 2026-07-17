"""Swappable workflow-execution backend registry (Phase 3)."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.executor.registry import (
    WORKFLOW_BACKENDS,
    WorkflowBackendRegistry,
    resolve_workflow_backend,
)


def _settings(**over) -> Settings:
    base = {"use_real_adapters": False}
    base.update(over)
    return Settings(**base)


def test_registered_names():
    assert WORKFLOW_BACKENDS.names() == ["argo", "local"]


def test_local_resolves_to_none_inline():
    assert resolve_workflow_backend(_settings(executor_backend="local")) is None
    # default (unset) is local
    assert resolve_workflow_backend(_settings()) is None


def test_argo_resolves_to_a_real_backend():
    from app.executor.argo import ArgoWorkflowExecutor

    backend = resolve_workflow_backend(_settings(executor_backend="argo"))
    assert isinstance(backend, ArgoWorkflowExecutor)


def test_unknown_backend_is_a_clear_error():
    with pytest.raises(ValueError, match="unknown executor_backend 'nomad'"):
        resolve_workflow_backend(_settings(executor_backend="nomad"))


def test_registry_is_extensible():
    reg = WorkflowBackendRegistry()
    reg.register("x", lambda _s: "backend-x")
    assert reg.create("x", _settings()) == "backend-x"
