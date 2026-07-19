"""packctl CLI.

  python -m packctl.cli validate <pack_dir>
  python -m packctl.cli lint     <pack_dir> [--strict]
  python -m packctl.cli install  <pack_dir> [--keep-going]

`validate` is pure/offline (manifest + component file structure). `lint` is also
pure/offline but goes deeper — component-file CONTENT + cross-references (see
lint.py) — and is what an author runs in CI. `install` drives the REAL platform
APIs against the locally-running stack, authorizing with harness-IdP-signed JWTs
(harness_auth.py). Exit code 0 only when every action succeeded.
"""

from __future__ import annotations

import argparse
import sys

from .client import Endpoints, PlatformClient
from .installer import install
from .lint import lint_pack
from .manifest import ManifestError, load_manifest


def _run_lint(pack_dir: str, strict: bool) -> int:
    """Print lint findings; exit 1 on any error (or any warning under --strict)."""
    report = lint_pack(pack_dir)
    for f in report.findings:
        loc = f"{f.file}{f.pointer}" if f.pointer not in ("", "/") else f.file
        mark = "ERROR" if f.severity == "error" else "warn "
        print(f"  [{mark}] {f.code} {f.kind}:{loc} — {f.message}")
    label = f"{report.pack}@{report.version}" if report.pack else pack_dir
    print(f"lint {label}: {len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    if report.errors:
        return 1
    return 1 if (strict and report.warnings) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="packctl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="validate pack.yaml + component files")
    v.add_argument("pack_dir")
    lc = sub.add_parser("lint", help="deep content + cross-reference lint (offline)")
    lc.add_argument("pack_dir")
    lc.add_argument("--strict", action="store_true", help="treat warnings as errors")
    i = sub.add_parser("install", help="install a pack into the running platform")
    i.add_argument("pack_dir")
    i.add_argument("--keep-going", action="store_true",
                   help="continue past failed components (default: stop)")
    args = ap.parse_args(argv)

    if args.cmd == "lint":
        return _run_lint(args.pack_dir, args.strict)

    try:
        manifest = load_manifest(args.pack_dir)
    except ManifestError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2
    print(f"manifest ok: {manifest.name}@{manifest.version} — "
          f"{len(manifest.components)} component file(s), "
          f"{len(manifest.deferred)} deferred")

    if args.cmd == "validate":
        return 0

    from . import harness_auth
    ctx = harness_auth.load_context()  # runs the idempotent platform seed
    author, approver, agent = harness_auth.token_providers(ctx)
    client = PlatformClient(
        endpoints=Endpoints(), tenant_id=ctx["tenant"],
        workspace_id=ctx["workspace"],
        author_token=author, approver_token=approver, agent_token=agent)
    result = install(manifest, client, keep_going=args.keep_going)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
