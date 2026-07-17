#!/bin/sh
# Windrose security baseline: SAST (semgrep, gosec, bandit), dependency/CVE
# (trivy), and secrets (gitleaks) over the whole platform. Reports land in
# security/reports/<date>/ as JSON plus a plain-text summary. Mirrors what
# .github/workflows/security-scan.yml runs in CI.
set -u
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP=$(date +%Y%m%d)
OUT="$ROOT/security/reports/$STAMP"
mkdir -p "$OUT"
cd "$ROOT"

echo "==> semgrep (Go/Python/TS security rules)"
semgrep scan \
  --config p/golang --config p/python --config p/typescript \
  --config p/security-audit --config p/secrets \
  --exclude '*.venv*' --exclude 'node_modules' --exclude '.pytest_cache' \
  --exclude 'deploy/e2e/run' --exclude '**/testdata' \
  --json --output "$OUT/semgrep.json" --metrics=off --quiet
python3 - "$OUT/semgrep.json" <<'EOF'
import json,sys,collections
d=json.load(open(sys.argv[1]))
sev=collections.Counter(r["extra"]["severity"] for r in d.get("results",[]))
print("semgrep findings:",dict(sev) or "none")
EOF

echo "==> gosec (all Go modules)"
: > "$OUT/gosec-summary.txt"
find services libs -name go.mod -maxdepth 3 2>/dev/null | while read -r mod; do
  dir=$(dirname "$mod")
  name=$(echo "$dir" | tr '/' '_')
  (cd "$dir" && gosec -quiet -no-fail -fmt json -out "$OUT/gosec-$name.json" ./... 2>/dev/null)
  python3 - "$OUT/gosec-$name.json" "$dir" <<'EOF' >> "$OUT/gosec-summary.txt"
import json,sys,collections
try: d=json.load(open(sys.argv[1]))
except Exception: print(f"{sys.argv[2]}: no report"); raise SystemExit
sev=collections.Counter(i["severity"] for i in d.get("Issues") or [])
print(f"{sys.argv[2]}: {dict(sev) or 'clean'}")
EOF
done
cat "$OUT/gosec-summary.txt"

echo "==> bandit (all Python sources)"
bandit -r services libs deploy packs \
  -x '**/.venv/**,**/node_modules/**,**/.pytest_cache/**' \
  -f json -o "$OUT/bandit.json" --quiet 2>/dev/null || true
python3 - "$OUT/bandit.json" <<'EOF'
import json,sys,collections
d=json.load(open(sys.argv[1]))
sev=collections.Counter(r["issue_severity"] for r in d.get("results",[]))
print("bandit findings:",dict(sev) or "none")
EOF

echo "==> trivy (dependency CVEs over lockfiles)"
trivy fs --scanners vuln --format json --output "$OUT/trivy.json" \
  --skip-dirs node_modules --skip-dirs .venv "$ROOT" >/dev/null 2>&1
python3 - "$OUT/trivy.json" <<'EOF'
import json,sys,collections
d=json.load(open(sys.argv[1]))
sev=collections.Counter(v["Severity"] for r in d.get("Results") or [] for v in r.get("Vulnerabilities") or [])
print("trivy CVEs:",dict(sev) or "none")
EOF

echo "==> gitleaks (secrets, filesystem mode)"
gitleaks dir "$ROOT" --report-format json --report-path "$OUT/gitleaks.json" \
  --exit-code 0 >/dev/null 2>&1
python3 - "$OUT/gitleaks.json" <<'EOF'
import json,sys,collections
try: d=json.load(open(sys.argv[1]))
except Exception: d=[]
rules=collections.Counter(f["RuleID"] for f in d)
print("gitleaks findings:",sum(rules.values()),dict(rules) or "")
EOF

echo "=== baseline complete: $OUT ==="
