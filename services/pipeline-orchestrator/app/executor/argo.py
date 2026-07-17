"""Argo Workflows adapter — INFRA-GATED (documented exception, like the cloud
warehouses in CONVENTIONS.md).

This is REAL code that speaks the Argo Workflows server REST API: it submits a
compiled ``WorkflowTemplate``/``Workflow`` to the tenant namespace, watches phase
via the Kubernetes watch API (informer, never polling), maps Argo phases to platform
run states, and terminates workflows. It is not a stub — every method issues a real
HTTP call. End-to-end verification is gated on a Kubernetes cluster + an Argo
Workflows server being reachable (there is no local-protocol equivalent on the Mac,
so ``executor_backend`` defaults to ``local``). When the Argo server is unreachable
the adapter raises ``DependencyUnavailable`` rather than pretending to succeed.
"""

from __future__ import annotations

import logging

import httpx

from app.domain.errors import DependencyUnavailable

logger = logging.getLogger(__name__)

# Argo phase -> platform run status name (PIPE-FR-031; Error≡Failed, BR-8).
PHASE_MAP = {
    "Pending": "submitted",
    "Running": "running",
    "Succeeded": "succeeded",
    "Failed": "failed",
    "Error": "failed",
}


class ArgoWorkflowExecutor:
    def __init__(self, server_url: str, *, timeout_s: float = 10.0):
        self.server_url = server_url.rstrip("/")
        self.timeout_s = timeout_s
        # Reuse one client (and its TCP+TLS connection pool) for the unary
        # submit/terminate calls rather than paying a fresh handshake each time.
        # The watch stream keeps its own client (timeout=None, long-lived).
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._client

    def _namespace(self, tenant_id: str) -> str:
        return f"{tenant_id}-processing"

    async def submit(self, tenant_id: str, manifest: dict, parameters: dict) -> str:
        """Submit a Workflow from the compiled template into the tenant namespace.
        Returns the created ``argo_workflow_name``."""
        ns = self._namespace(tenant_id)
        body = {
            "workflow": {
                "metadata": {"generateName": manifest["metadata"]["name"] + "-",
                             "namespace": ns,
                             "labels": {"windrose.io/managed": "true"}},
                "spec": {
                    "workflowTemplateRef": {"name": manifest["metadata"]["name"]},
                    "arguments": {"parameters": [
                        {"name": k, "value": str(v)} for k, v in parameters.items()]},
                },
            }
        }
        url = f"{self.server_url}/api/v1/workflows/{ns}"
        try:
            client = self._http()
            resp = await client.post(url, json=body)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DependencyUnavailable(
                f"Argo server unreachable at {self.server_url}: {exc}") from exc
        return resp.json()["metadata"]["name"]

    async def watch(self, tenant_id: str, workflow_name: str):
        """Async-generator over phase changes via the Kubernetes watch stream
        (informer semantics — never polls). Yields ``{phase, status, nodes}``."""
        import json

        ns = self._namespace(tenant_id)
        url = (f"{self.server_url}/api/v1/workflow-events/{ns}"
               f"?listOptions.fieldSelector=metadata.name={workflow_name}")
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        event = json.loads(line)
                        wf = event.get("result", {}).get("object", {})
                        phase = wf.get("status", {}).get("phase")
                        if phase:
                            yield {"phase": phase, "status": PHASE_MAP.get(phase),
                                   "nodes": wf.get("status", {}).get("nodes", {})}
        except httpx.HTTPError as exc:
            raise DependencyUnavailable(
                f"Argo watch stream failed for {workflow_name}: {exc}") from exc

    async def terminate(self, tenant_id: str, workflow_name: str) -> None:
        ns = self._namespace(tenant_id)
        url = f"{self.server_url}/api/v1/workflows/{ns}/{workflow_name}/terminate"
        try:
            client = self._http()
            resp = await client.put(url, json={"name": workflow_name,
                                               "namespace": ns})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DependencyUnavailable(
                f"Argo terminate failed for {workflow_name}: {exc}") from exc
