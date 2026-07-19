"""The pack catalog — read LIVE from the on-disk packs/ directory.

A real deployment resolves packs from the signed OCI registry (PKG-FR-005);
here the catalog is the repo's `packs/` tree, validated through packctl's
manifest loader (the same validation packctl runs), so the service never
invents a pack it can't actually read.
"""

from __future__ import annotations

import functools
import sys
from pathlib import Path


@functools.cache
def _packctl():
    """Import packctl (manifest + client) from the packs dir once."""
    # packs_dir contains both the pack directories AND the `packctl` package.
    packs_dir = _packs_root()
    if str(packs_dir) not in sys.path:
        sys.path.insert(0, str(packs_dir))
    from packctl import client, manifest  # noqa: PLC0415

    return manifest, client


_ROOT: Path | None = None


def configure(packs_dir: str) -> None:
    global _ROOT
    _ROOT = Path(packs_dir).resolve()


def _packs_root() -> Path:
    if _ROOT is not None:
        return _ROOT
    # Default: repo-root/packs relative to this file (services/pack-service/app/...).
    return (Path(__file__).resolve().parents[3] / "packs").resolve()


def _manifest_mod():
    return _packctl()[0]


def list_packs() -> list[dict]:
    """Every valid pack in the catalog as a summary (name/version/components)."""
    manifest = _manifest_mod()
    root = _packs_root()
    out: list[dict] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not (child / "pack.yaml").is_file():
            continue
        try:
            m = manifest.load_manifest(child)
        except Exception:  # noqa: BLE001 - a malformed pack is simply not listed
            continue
        out.append(_summary(m))
    return out


def get_pack(name: str) -> dict | None:
    manifest = _manifest_mod()
    root = _packs_root()
    pack_dir = root / name
    if not (pack_dir / "pack.yaml").is_file():
        return None
    m = manifest.load_manifest(pack_dir)
    detail = _summary(m)
    detail["deferred"] = [{"kind": d.get("kind"), "reason": d.get("reason", "")} for d in m.deferred]  # noqa: E501
    return detail


def load_manifest(name: str):
    """The validated Manifest object for a pack (raises if missing/invalid)."""
    manifest = _manifest_mod()
    root = _packs_root()
    pack_dir = root / name
    return manifest.load_manifest(pack_dir)


def lint_pack(name: str) -> dict | None:
    """Run the pack-authoring linter (packctl.lint) over one catalog pack and
    return its report as a dict, or None if the pack is absent. Pure/offline —
    the same deep content + cross-reference checks `packctl lint` runs in CI."""
    _packctl()  # ensure the packs dir (with packctl) is importable
    from packctl.lint import lint_pack as _lint  # noqa: PLC0415

    pack_dir = _packs_root() / name
    if not (pack_dir / "pack.yaml").is_file():
        return None
    return _lint(pack_dir).as_dict()


def _summary(m) -> dict:
    counts: dict[str, int] = {}
    for c in m.components:
        counts[c.kind] = counts.get(c.kind, 0) + 1
    return {
        "name": m.name,
        "version": m.version,
        "description": m.description,
        "publisher": m.publisher,
        "categories": list(m.categories),
        "regulatory": list(m.regulatory),
        "components": counts,
        "deferred_kinds": [d.get("kind") for d in m.deferred],
    }
