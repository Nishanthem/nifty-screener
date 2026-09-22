#!/usr/bin/env bash
# Full live trading-day routine.
#   - polls snapshots from 09:15 to establish the opening range (first 15-min
#     candle, 09:15-09:30) and build the 5-min candles after it
#   - at 09:35, 09:40 and 09:45 IST scans the 5-min candle that just closed:
#     close above OR-high -> long breakout, below OR-low -> short breakout
#     (side still follows the index vs its VWAP)
#   - the 09:45 scan is the final ranked pick; earlier scans are early alerts
# Requires Chrome running with CDP on localhost:29229 (NSE blocks raw requests).
set -euo pipefail
cd "$(dirname "$0")"

SIDE="${SIDE:-auto}"   # auto | long | short
SCANS=(0935 0940 0945)
FINAL=0945

echo "polling snapshots until ${FINAL} IST (15m opening range + 5m candles)..."
last=0
while :; do
  now="$(TZ=Asia/Kolkata date +%H%M)"
  python3 -m app.cli --poll || true
  due=0
  for t in "${SCANS[@]}"; do
    [ "$now" -ge "$t" ] && [ "$t" -gt "$last" ] && due="$t"
  done
  if [ "$due" -ne 0 ]; then
    echo "--- 5m ORB scan @ ${due} IST (now $now) ---"
    # every stock that closed a 5m candle above/below its OR (both sides), then the gated ranked picks
    python3 -m app.cli --breakouts || true
    if [ "$due" -ge "$FINAL" ]; then
      python3 -m app.cli --live --scan --top 3 --side "$SIDE"
      echo "=== SCAN ${due} DONE (final) ==="
      break
    fi
    python3 -m app.cli --live --scan --top 3 --side "$SIDE" || true
    echo "=== SCAN ${due} DONE ==="
    last="$due"
  fi
  sleep 60
done
