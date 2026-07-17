"""Canonical JSON + args digest — byte-compatible with tool-plane's Go
``domain.ArgsDigest`` (services/tool-plane/internal/domain/urn.go).

tool-plane binds a proposal-execution grant to ``args_digest =
sha256(canonicalJSON(args))``. If our digest disagrees with theirs by a single
byte the grant is rejected, so this MUST reproduce Go's ``encoding/json``
behaviour exactly:

* objects: keys sorted (byte order), ``{"k":v,...}`` with no spaces;
* arrays: ``[v,...]`` no spaces;
* strings: Go ``json.Marshal`` emits **raw UTF-8** (it does NOT escape non-ASCII
  to ``\\uXXXX``); with HTML escaping on (the default) it escapes only ``<``,
  ``>``, ``&`` and the two line/paragraph separators U+2028/U+2029. So we use
  ``json.dumps(ensure_ascii=False)`` and post-process exactly those five chars —
  accented names ("Zürich Re"), currency (€/£/¥) and non-English notes therefore
  hash identically on both sides (money-path correctness, not just ASCII);
* numbers/bools/null: Go and Python agree for the JSON produced by tool-plane's
  MCP argument decode (integers, floats, bool, null).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

_LS = chr(0x2028)  # line separator — Go escapes this even in raw-UTF-8 mode
_PS = chr(0x2029)  # paragraph separator — likewise


def _encode(value: Any) -> str:
    if isinstance(value, dict):
        parts = [_encode_str(str(k)) + ":" + _encode(value[k]) for k in sorted(value.keys())]
        return "{" + ",".join(parts) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_encode(v) for v in value) + "]"
    if isinstance(value, str):
        return _encode_str(value)
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return json.dumps(value, separators=(",", ":"))


def _encode_str(s: str) -> str:
    # Match Go encoding/json: raw UTF-8, HTML-escape < > &, plus U+2028/U+2029.
    out = json.dumps(s, ensure_ascii=False)
    out = out.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return out.replace(_LS, "\\u2028").replace(_PS, "\\u2029")


def canonical_json(args: dict[str, Any]) -> bytes:
    """Deterministic, sorted-key, whitespace-free JSON bytes (Go-compatible)."""
    return _encode(args or {}).encode()


def args_digest(args: dict[str, Any]) -> str:
    """SHA-256 hex over canonical JSON of args (tool-plane grant binding)."""
    return hashlib.sha256(canonical_json(args)).hexdigest()
