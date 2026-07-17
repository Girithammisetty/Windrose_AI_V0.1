"""URN helpers — MASTER-FR-013 / BRD §4.2.

Forms: wr:<tenant>:<service>:<resource_type>/<resource_id>
Dataset versions: wr:<tenant>:dataset:version/<dataset_id>@v<no>
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.errors import ValidationFailed

_URN_RE = re.compile(
    r"^wr:(?P<tenant>[A-Za-z0-9-]+):(?P<service>[a-z0-9_-]+):"
    r"(?P<rtype>[a-z0-9_-]+)/(?P<rid>[A-Za-z0-9@._:-]+)$"
)


@dataclass(frozen=True, slots=True)
class Urn:
    tenant: str
    service: str
    rtype: str
    rid: str

    def __str__(self) -> str:
        return f"wr:{self.tenant}:{self.service}:{self.rtype}/{self.rid}"


def parse_urn(value: str) -> Urn:
    m = _URN_RE.match(value or "")
    if not m:
        raise ValidationFailed(f"invalid URN: {value!r}")
    return Urn(m["tenant"], m["service"], m["rtype"], m["rid"])


def is_valid_urn(value: str) -> bool:
    return bool(_URN_RE.match(value or ""))


def dataset_urn(tenant_id: str, dataset_id: str) -> str:
    return f"wr:{tenant_id}:dataset:dataset/{dataset_id}"


def version_urn(tenant_id: str, dataset_id: str, version_no: int) -> str:
    return f"wr:{tenant_id}:dataset:version/{dataset_id}@v{version_no}"


def parse_version_urn(urn: Urn) -> tuple[str, int] | None:
    """Return (dataset_id, version_no) for wr:*:dataset:version/<id>@v<no>."""
    if urn.service != "dataset" or urn.rtype != "version" or "@v" not in urn.rid:
        return None
    dataset_id, _, no = urn.rid.rpartition("@v")
    try:
        return dataset_id, int(no)
    except ValueError:
        return None
