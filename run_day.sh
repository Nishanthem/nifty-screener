#!/usr/bin/env bash
# Full live trading-day routine.
#   - polls snapshots from 09:15 to establish the opening range (first 15-min
#     candle, 09:15-09:30) and build the 5-min candles after it
#   - 09:25:10 IST (sharp, 10s after the 10-min range closes): early alert,
#     current price vs the first-10-min range (09:15-09:25)
#   - 09:32 IST: early alert, current price vs the 15-min range (09:15-09:30)
#   - at 09:35, 09:40 and 09:45 IST scans the 5-min candle that just closed:
#     close above OR-high -> long breakout, below OR-low -> short breakout
#     (side gated by Nifty vs prev close: >=+0.3% longs, <=-0.3% shorts, else both)
#   - the 09:45 scan is the final ranked pick; earlier scans are early alerts
# Requires Chrome running with CDP on localhost:29229 (NSE blocks raw requests).
set -euo pipefail
cd "$(dirname "$0")"

SIDE="${SIDE:-auto}"   # auto | long | short
SCANS=(092510 093200 093500 094000 094500)   # HHMMSS IST
declare -A CMP_OR=([092510]=10 [093200]=15)   # scan time -> OR minutes for CMP scans
FIRST=092510   # the loop sleeps to this exact second instead of the next 1/min poll
FINAL=094500

echo "polling snapshots until ${FINAL} IST (15m opening range + 5m candles)..."
last=0
while :; do
  now="$(TZ=Asia/Kolkata date +%H%M%S)"
  due=0
  for t in "${SCANS[@]}"; do
    [ "$now" -ge "$t" ] && [ "$t" -gt "$last" ] && due="$t"
  done
  # CMP scans take (and persist) their own fresh snapshot; skip the poll so they fire on time
  if [ "$due" -eq 0 ] || [ -z "${CMP_OR[$due]:-}" ]; then
    python3 -m app.cli --poll || true
  fi
  if [ "$due" -ne 0 ] && [ -n "${CMP_OR[$due]:-}" ]; then
    m="${CMP_OR[$due]}"
    echo "--- CMP scan vs ${m}m OR @ ${due} IST (now $now) ---"
    python3 -m app.cli --cmp-scan "$m" --side "$SIDE" || true
    python3 -m app.yf_orb --cmp "$m" || true
    echo "=== SCAN ${due:0:4} DONE ==="
    last="$due"
  elif [ "$due" -ne 0 ]; then
    echo "--- 5m ORB scan @ ${due} IST (now $now) ---"
    # every stock that closed a 5m candle above/below its OR (both sides), then the gated ranked picks
    python3 -m app.cli --breakouts || true
    # yfinance-based list of the same breakouts (fallback when NSE blocks the live path)
    python3 -m app.yf_orb || true
    if [ "$due" -ge "$FINAL" ]; then
      python3 -m app.cli --live --scan --top 3 --side "$SIDE" \
        || python3 -m app.cli --top 3 --time 09:45 --side "$SIDE"
      echo "=== SCAN ${due:0:4} DONE (final) ==="
      break
    fi
    python3 -m app.cli --live --scan --top 3 --side "$SIDE" || true
    echo "=== SCAN ${due:0:4} DONE ==="
    last="$due"
  fi
  nap=60
  if [ "$last" -lt "$FIRST" ]; then
    left=$(( $(TZ=Asia/Kolkata date -d "today ${FIRST:0:2}:${FIRST:2:2}:${FIRST:4:2}" +%s) - $(date +%s) ))
    [ "$left" -gt 0 ] && [ "$left" -lt "$nap" ] && nap="$left"
  fi
  sleep "$nap"
done
