#!/usr/bin/env bash
# 09:30 opening-reversal scan (long-tailed 5m candles + RSI / prev-day level).
#   - polls live snapshots 09:15-09:30 to rebuild the 09:15/09:20/09:25 candles
#   - prints the graded scan at 09:30 IST
# Requires Chrome running with CDP on localhost:29229 (NSE blocks raw requests).
# Without Chrome, use the delayed path instead: python3 -m app.cli --reversal
set -euo pipefail
cd "$(dirname "$0")"

echo "polling snapshots until 09:30 IST (first three 5m candles)..."
while [ "$(TZ=Asia/Kolkata date +%H%M)" -lt 930 ]; do
  python3 -m app.cli --poll || true
  sleep 60
done

python3 -m app.cli --reversal --live
