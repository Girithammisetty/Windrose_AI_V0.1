"""Resource URN helpers (MASTER-FR-013): wr:<tenant>:<service>:<type>/<id>."""

from __future__ import annotations

from dataclasses import dataclass


def job_urn(tenant_id: str, job_id: str) -> str:
    return f"wr:{tenant_id}:inference:job/{job_id}"


def schedule_urn(tenant_id: str, schedule_id: str) -> str:
    return f"wr:{tenant_id}:inference:schedule/{schedule_id}"


def endpoint_urn(tenant_id: str, endpoint_id: str) -> str:
    return f"wr:{tenant_id}:inference:endpoint/{endpoint_id}"


def dataset_urn(tenant_id: str, dataset_id: str) -> str:
    return f"wr:{tenant_id}:dataset:dataset/{dataset_id}"


def model_version_urn(tenant_id: str, model_id: str, version: int) -> str:
    return f"wr:{tenant_id}:experiment:model_version/{model_id}@{version}"


def model_urn(tenant_id: str, model_id: str) -> str:
    return f"wr:{tenant_id}:experiment:model/{model_id}"


@dataclass(slots=True)
class ParsedUrn:
    tenant_id: str
    service: str
    resource_type: str
    resource_id: str
    version: int | None = None


def parse(urn: str) -> ParsedUrn:
    """Parse a Windrose URN. Model-version URNs may carry an ``@<version>`` tail."""
    parts = urn.split(":")
    if len(parts) != 4 or parts[0] != "wr":
        raise ValueError(f"malformed urn {urn!r}")
    _, tenant_id, service, tail = parts
    if "/" not in tail:
        raise ValueError(f"malformed urn tail {urn!r}")
    resource_type, resource_id = tail.split("/", 1)
    version: int | None = None
    if "@" in resource_id:
        resource_id, ver = resource_id.rsplit("@", 1)
        try:
            version = int(ver)
        except ValueError as exc:
            raise ValueError(f"malformed version in urn {urn!r}") from exc
    return ParsedUrn(tenant_id, service, resource_type, resource_id, version)
