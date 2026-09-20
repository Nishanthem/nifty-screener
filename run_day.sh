#!/usr/bin/env bash
# Full live trading-day routine.
#   - polls snapshots 09:15-10:00 to establish the true opening range and
#     build the first three 15-min candles (long-wick check)
#   - prints the ranked pick (long or short, following the index) at 10:00 IST
# Requires Chrome running with CDP on localhost:29229 (NSE blocks raw requests).
set -euo pipefail
cd "$(dirname "$0")"

SIDE="${SIDE:-auto}"   # auto | long | short

echo "polling snapshots until 10:00 IST (opening range + first three 15m candles)..."
while [ "$(TZ=Asia/Kolkata date +%H%M)" -lt 1000 ]; do
  python3 -m app.cli --poll || true
  sleep 60
done

python3 -m app.cli --live --top 3 --side "$SIDE"
