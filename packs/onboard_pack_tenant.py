#!/usr/bin/env python3
"""Onboard ONE new tenant with a chosen vertical pack — the parameterized
single-tenant version of install_packs_multitenant.py, for spinning up an
extra isolated tenant of any vertical on demand (e.g. a second AML bank to
test cross-tenant isolation, or a fresh demo tenant for a customer session).

Everything flows through the same real paths as the batch script:
identity-service tenant provisioning (stable name, idempotent), rbac tenant
bootstrap + Admin operators, packctl pack install over Core public APIs
(library pack layered first where BRD 31 requires), one test user per pack
role via rbac's member API with live capability verification, dev-login map
merge, ui-web restart, and the shared cheat sheet (MULTITENANT_LOGINS.md).

Examples:
  # a second banking-aml tenant named "First National"
  ../deploy/e2e/.venv/bin/python onboard_pack_tenant.py \
      --pack banking-aml --tenant fn-bank --display "First National Bank"

  # short label (login email domain) defaults to the tenant name's last word;
  # override it explicitly:
  ../deploy/e2e/.venv/bin/python onboard_pack_tenant.py \
      --pack payer-fwa-siu --tenant acme-siu --display "Acme SIU" --short acmesiu
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import install_packs_multitenant as base  # noqa: E402


def main() -> int:
    known_packs = sorted({p[0] for p in base.PACKS} | {base.LIBRARY_PACK})
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pack", required=True, choices=known_packs,
                    help="pack directory to install")
    ap.add_argument("--tenant", required=True,
                    help="stable tenant name (idempotent — reused if it exists)")
    ap.add_argument("--display", required=True, help="tenant display name")
    ap.add_argument("--short", default=None,
                    help="login email domain label (default: derived from --tenant); "
                         "logins become <role>@<short>.windrose")
    ap.add_argument("--no-restart-ui", action="store_true",
                    help="do not restart ui-web (logins go live on next UI boot)")
    args = ap.parse_args()

    short = args.short or re.sub(r"[^a-z0-9]", "", args.tenant.split("-")[-1].lower())
    if not short:
        base.die("--short could not be derived from --tenant; pass it explicitly")
    if args.pack == base.LIBRARY_PACK:
        base.warn(f"{base.LIBRARY_PACK} is a LIBRARY pack (BRD 31) with no surface "
                  "of its own — installing it standalone gives you only its roles, "
                  "dispositions and grounding memories.")

    entries, row = base.onboard_tenant(args.pack, args.tenant, args.display, short)
    base.merge_personas(entries)
    base.write_report([row])
    if not args.no_restart_ui:
        base.restart_ui()

    print()
    base.say(f"{base.G}done{base.N} — tenant {args.tenant!r} "
             f"({row['tenant_id']}) with {len(row['users'])} role user(s)")
    print(f"  login: admin@{short}.windrose (any password) at http://localhost:3000/login")
    print(f"  all logins: packs/MULTITENANT_LOGINS.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
