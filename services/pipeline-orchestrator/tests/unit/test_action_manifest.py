"""BUG-1 guard: every action a route GUARDS must be in the manifest we REGISTER with
rbac, and every action must use a canonical rbac verb (`<service>.<resource>.<verb>`).
A mismatch means rbac's OPA catalog won't know the guarded action → action_known=False
→ 403 even for a tenant admin."""

from __future__ import annotations

from app.registration import MANIFEST

# The canonical rbac verb set (master §2.x action taxonomy).
CANONICAL_VERBS = {"read", "list", "create", "update", "delete", "execute", "assign",
                   "approve", "admin", "export", "share"}


def _iter_routes(routes):
    """FastAPI wraps include_router() results in lazy _IncludedRouter objects exposing
    the real APIRouter via ``original_router``; flatten recursively to the APIRoutes."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        nested = getattr(route, "routes", None)
        if inner is not None and getattr(inner, "routes", None):
            yield from _iter_routes(inner.routes)
        elif nested:
            yield from _iter_routes(nested)
        else:
            yield route


def _scan_closure(fn, actions: set[str]) -> None:
    for cell in getattr(fn, "__closure__", None) or []:
        try:
            val = cell.cell_contents
        except ValueError:
            continue
        if isinstance(val, str) and val.startswith("pipeline."):
            actions.add(val)


def _guarded_actions(app) -> set[str]:
    actions: set[str] = set()
    for route in _iter_routes(app.routes):
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        stack = list(dependant.dependencies)
        while stack:
            dep = stack.pop()
            if getattr(dep, "call", None) is not None:
                _scan_closure(dep.call, actions)
            stack.extend(getattr(dep, "dependencies", []) or [])
    return actions


def test_every_guarded_action_is_registered(app):
    guarded = _guarded_actions(app)
    assert guarded, "no guarded actions discovered — introspection broke"
    missing = guarded - set(MANIFEST)
    assert not missing, f"routes guard actions not in the registered manifest: {missing}"


def test_manifest_has_no_unused_actions(app):
    # Every registered action should actually be guarded somewhere (no dead entries).
    guarded = _guarded_actions(app)
    unused = set(MANIFEST) - guarded
    assert not unused, f"manifest declares actions no route guards: {unused}"


def test_all_actions_use_canonical_verbs():
    for action in MANIFEST:
        parts = action.split(".")
        assert len(parts) == 3, f"{action} is not <service>.<resource>.<verb>"
        service, resource, verb = parts
        assert service == "pipeline"
        assert verb in CANONICAL_VERBS, f"{action} uses non-canonical verb {verb!r}"
