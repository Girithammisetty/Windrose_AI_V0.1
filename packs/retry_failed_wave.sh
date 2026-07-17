#!/bin/sh
# Retry the 12 packs that failed in the first batch (iceberg sqlite lock +
# the trade-compliance tag bug, both fixed). Idempotent re-runs; 3s pacing
# between packs to be gentle on the sqlite-backed iceberg catalog.
set -u
PY=../deploy/e2e/.venv/bin/python
LOG_DIR=.install-logs
mkdir -p "$LOG_DIR"
fail=0

install_one() {
  pack="$1"; tenant="$2"; display="$3"; short="$4"; restart="$5"
  extra="--no-restart-ui"
  [ "$restart" = "restart" ] && extra=""
  echo "==> $pack -> $tenant"
  if $PY onboard_pack_tenant.py --pack "$pack" --tenant "$tenant" \
       --display "$display" --short "$short" $extra \
       > "$LOG_DIR/$pack.retry.log" 2>&1; then
    grep -E "installed: " "$LOG_DIR/$pack.retry.log" | tail -2
  else
    echo "  FAIL $pack — see $LOG_DIR/$pack.retry.log"
    tail -5 "$LOG_DIR/$pack.retry.log"
    fail=1
  fi
  sleep 3
}

install_one trade-compliance         wr-trade        "Windrose Trade Compliance"      trade        no
install_one trust-safety-appeals     wr-appeals      "Windrose Trust & Safety"        appeals      no
install_one device-complaints        wr-mdr          "Windrose Device Vigilance"      mdr          no
install_one underwriting-intake      wr-uw           "Windrose Underwriting Intake"   uw           no
install_one chargeback-representment wr-merchant     "Windrose Merchant Disputes"     merchant     no
install_one seller-vetting           wr-marketplace  "Windrose Marketplace Integrity" marketplace  no
install_one benefits-appeals         wr-benefits     "Windrose Benefits Adjudication" benefits     no
install_one utility-inspections      wr-utility      "Windrose Utility Inspections"   utility      no
install_one construction-claims      wr-construction "Windrose Construction Claims"   construction no
install_one ap-invoice-audit         wr-apaudit      "Windrose AP Audit"              apaudit      no
install_one manufacturing-mrb        wr-mrb          "Windrose Manufacturing Quality" mrb          no
install_one tax-notices              wr-tax          "Windrose Tax Notices"           tax          restart

echo "=== retry done, fail=$fail ==="
exit $fail
