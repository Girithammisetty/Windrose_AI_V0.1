"""Resource URNs (MASTER-FR-013): wr:<tenant>:<service>:<type>/<id>."""

from __future__ import annotations

SERVICE = "memory"


def memory_urn(tenant_id: str, memory_id: str) -> str:
    return f"wr:{tenant_id}:{SERVICE}:memory/{memory_id}"


def corpus_urn(tenant_id: str, corpus_key: str) -> str:
    return f"wr:{tenant_id}:{SERVICE}:corpus/{corpus_key}"


def chunk_urn(tenant_id: str, chunk_id: str) -> str:
    return f"wr:{tenant_id}:{SERVICE}:chunk/{chunk_id}"


def erasure_urn(tenant_id: str, request_id: str) -> str:
    return f"wr:{tenant_id}:{SERVICE}:erasure/{request_id}"


def session_urn(tenant_id: str, session_id: str) -> str:
    return f"wr:{tenant_id}:{SERVICE}:session/{session_id}"
