#!/bin/bash
# Full offline suite. Optional: AIMM_LIB=<dir with ai_marketplace_monitor+playwright> enables the real-AIMM validator and browser tests;
# LD_LIBRARY_PATH may need the few system libs Chromium wants (see docs). No Docker, no network, no Facebook, no Telegram.
cd "$(dirname "$0")/.." || exit 1
rc=0
for t in test_settings test_connection test_forwarder test_guard test_agent test_logflood test_recap test_scan_diag test_lifecycle test_panel test_session_patch test_ui_browser; do
  printf '%-22s' "$t"; out=$(python3 tests/$t.py 2>&1); r=$?; echo "$out" | tail -1; [ $r -ne 0 ] && { rc=1; echo "$out" | grep FAIL; }
done
exit $rc
