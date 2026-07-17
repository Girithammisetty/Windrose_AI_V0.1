"""Resource URNs (MASTER-FR-013): wr:<tenant>:<service>:<rtype>/<rid>."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.errors import ValidationFailed

SERVICE = "experiment"


@dataclass(slots=True)
class ParsedUrn:
    tenant: str
    service: str
    rtype: str
    rid: str


def parse_urn(urn: str) -> ParsedUrn:
    try:
        prefix, tenant, service, tail = urn.split(":", 3)
        rtype, rid = tail.split("/", 1)
    except ValueError as exc:
        raise ValidationFailed(f"invalid URN {urn!r}") from exc
    if prefix != "wr" or not tenant or not service or not rtype or not rid:
        raise ValidationFailed(f"invalid URN {urn!r}")
    return ParsedUrn(tenant=tenant, service=service, rtype=rtype, rid=rid)


def is_valid_urn(urn: str) -> bool:
    try:
        parse_urn(urn)
        return True
    except ValidationFailed:
        return False


def experiment_urn(tenant: str, experiment_id: str) -> str:
    return f"wr:{tenant}:{SERVICE}:experiment/{experiment_id}"


def run_urn(tenant: str, run_id: str) -> str:
    return f"wr:{tenant}:{SERVICE}:run/{run_id}"


def model_urn(tenant: str, model_id: str) -> str:
    return f"wr:{tenant}:{SERVICE}:model/{model_id}"


def model_version_urn(tenant: str, model_id: str, version: int) -> str:
    # `@<version>` is the platform-wide convention (inference-service's own
    # domain/urn.py parse() only recognizes an `@` tail, not `.`) — this used
    # to emit `.{version}`, silently breaking every inference-job submission
    # ("model_version_urn must reference a model_version@<n>") since no
    # existing model-version URN inference-service ever saw could parse.
    return f"wr:{tenant}:{SERVICE}:model_version/{model_id}@{version}"


def promotion_urn(tenant: str, promotion_id: str) -> str:
    return f"wr:{tenant}:{SERVICE}:promotion/{promotion_id}"
