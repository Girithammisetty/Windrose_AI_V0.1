#!/usr/bin/env python3
"""Render the Helm PrometheusRule template into a plain Prometheus rule file.

Why this exists: deploy/helm/datacern/templates/prometheusrule.yaml is the
single source of truth for the platform's SLO alert bundle (BRD 58 WS2), but
it is a Kubernetes CRD (`kind: PrometheusRule`) -- a real Prometheus server
run with `docker run prom/prometheus ...` cannot load a CRD directly, it needs
a plain rule file with a top-level `groups:` key (Prometheus's own
`rule_files:` format).

This script runs `helm template ... --show-only templates/prometheusrule.yaml`
(the exact same rendering path `helm lint`/CI use) and lifts `.spec.groups`
out of the rendered CRD into deploy/observability/rules.generated.yml. No rule
body (name/expr/for/labels/annotations) is hand-duplicated anywhere -- if the
Helm template changes, re-running this script picks up the change with zero
drift risk between "what CI lints" and "what the drill actually evaluates".

Usage:
    deploy/e2e/.venv/bin/python deploy/observability/render_rules.py
    (or plain `python3` if PyYAML is already on PATH -- yq is NOT used, see
    deploy/observability/drill.sh)
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit(
        "PyYAML is required. Run with deploy/e2e/.venv/bin/python, e.g.:\n"
        "  deploy/e2e/.venv/bin/python deploy/observability/render_rules.py"
    )

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART_DIR = REPO_ROOT / "deploy" / "helm" / "datacern"
OUT_PATH = pathlib.Path(__file__).resolve().parent / "rules.generated.yml"


def render() -> dict:
    cmd = [
        "helm", "template", "datacern", str(CHART_DIR),
        "--show-only", "templates/prometheusrule.yaml",
        "--set", "observability.prometheusRule.enabled=true",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        sys.exit(
            f"helm template failed (exit {proc.returncode}):\n{proc.stderr}"
        )
    if not proc.stdout.strip():
        sys.exit("helm template produced no output -- is helm on PATH?")
    return yaml.safe_load(proc.stdout)


def main() -> None:
    doc = render()
    if not doc or "spec" not in doc or "groups" not in doc.get("spec", {}):
        sys.exit(
            "rendered PrometheusRule has no .spec.groups -- template changed "
            "shape or --set observability.prometheusRule.enabled=true didn't "
            "take effect; refusing to write an empty/stale rule file."
        )
    groups = doc["spec"]["groups"]
    n_rules = sum(len(g.get("rules", [])) for g in groups)

    plain = {"groups": groups}
    header = (
        "# GENERATED FILE -- do not hand-edit.\n"
        "# Source of truth: deploy/helm/datacern/templates/prometheusrule.yaml\n"
        "# Regenerate with: deploy/e2e/.venv/bin/python deploy/observability/render_rules.py\n"
        "# (extracted via `helm template --show-only templates/prometheusrule.yaml`,\n"
        "# lifting the CRD's .spec.groups into plain Prometheus rule_files format.)\n"
    )
    with OUT_PATH.open("w") as f:
        f.write(header)
        yaml.safe_dump(plain, f, default_flow_style=False, sort_keys=False)

    print(f"wrote {OUT_PATH} ({len(groups)} groups, {n_rules} rules)")


if __name__ == "__main__":
    main()
