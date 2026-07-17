"""BUG-1: the actions the routes GUARD must exactly match the canonical action
manifest experiment-service REGISTERS with rbac (no drift), and every name must
be catalog-canonical `<service>.<resource>.<verb>` with an allowed verb."""

from __future__ import annotations

import re
from pathlib import Path

from app.registration import MANIFEST

ROUTES_DIR = Path(__file__).resolve().parents[2] / "app" / "api" / "routes"
_GUARD_RE = re.compile(r'require\(\s*"([^"]+)"\s*\)')

# Canonical verb set (MASTER-FR-016 catalog vocabulary).
ALLOWED_VERBS = {
    "read", "list", "create", "update", "delete", "execute", "assign",
    "approve", "admin", "export", "share",
}


def _guarded_actions() -> set[str]:
    actions: set[str] = set()
    for path in ROUTES_DIR.glob("*.py"):
        actions.update(_GUARD_RE.findall(path.read_text()))
    return actions


def test_every_guarded_action_is_registered():
    guarded = _guarded_actions()
    assert guarded, "no route guards found — regex/layout drift"
    missing = guarded - set(MANIFEST)
    assert not missing, f"routes guard actions not in the registered manifest: {sorted(missing)}"


def test_no_stale_manifest_entries():
    # every registered action should actually be guarded by some route (else the
    # catalog advertises a permission nothing enforces).
    guarded = _guarded_actions()
    unused = set(MANIFEST) - guarded
    assert not unused, f"manifest declares actions no route guards: {sorted(unused)}"


def test_all_actions_are_catalog_canonical():
    for action in MANIFEST:
        parts = action.split(".")
        assert len(parts) == 3, f"{action!r} is not <service>.<resource>.<verb>"
        service, _resource, verb = parts
        assert service == "experiment", f"{action!r} wrong service segment"
        assert verb in ALLOWED_VERBS, f"{action!r} uses a non-canonical verb {verb!r}"
