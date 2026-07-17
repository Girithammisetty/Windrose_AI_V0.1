"""BUG-1 guard: every action a ROUTE guards must be a canonical action REGISTERED
with rbac (MANIFEST). A mismatch makes OPA's catalog report action_known=False →
403 even for a tenant admin."""

from __future__ import annotations

import inspect
import re

from app.api.routes import inferences, lineage, schedules
from app.registration import MANIFEST

CANONICAL_VERBS = {
    "read", "list", "create", "update", "delete", "execute", "assign", "approve",
    "admin", "export", "share",
}

_REQUIRE_RE = re.compile(r'require\(\s*"(inference\.[^"]+)"\s*\)')


def _guarded_actions() -> set[str]:
    """Every action string passed to a ``require(...)`` route guard across the
    route modules."""
    actions: set[str] = set()
    for module in (inferences, schedules, lineage):
        actions.update(_REQUIRE_RE.findall(inspect.getsource(module)))
    return actions


def test_every_guarded_action_is_registered():
    guarded = _guarded_actions()
    assert guarded, "no route guards discovered"
    missing = guarded - set(MANIFEST)
    assert not missing, f"routes guard actions absent from the rbac manifest: {missing}"


def test_guarded_actions_are_all_canonical_verbs():
    for action in _guarded_actions():
        verb = action.split(".")[2]
        assert verb in CANONICAL_VERBS, f"route guards non-canonical verb: {action!r}"


def test_manifest_actions_are_canonical():
    for action in MANIFEST:
        parts = action.split(".")
        assert len(parts) == 3, f"non-canonical action {action!r}"
        assert parts[0] == "inference"
        verb = parts[2]
        # base verb (allow a fine-grained capability suffix like create_unpromoted)
        assert verb.split("_")[0] in CANONICAL_VERBS, f"non-canonical verb in {action!r}"


def test_no_legacy_submit_action_registered():
    # 'submit' / 'cancel' are NOT canonical verbs and must not appear
    for action in MANIFEST:
        assert not action.endswith(".submit")
        assert not action.endswith(".cancel")
