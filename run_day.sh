#!/usr/bin/env bash
# Full live trading-day routine.
#   - polls snapshots 09:15-09:30 to establish the true opening range
#   - prints the ranked pick at 10:00 IST
# Requires Chrome running with CDP on localhost:29229 (NSE blocks raw requests).
set -euo pipefail
cd "$(dirname "$0")"

echo "polling opening range until 09:30 IST..."
while [ "$(TZ=Asia/Kolkata date +%H%M)" -lt 0930 ]; do
  python3 -m app.cli --poll || true
  sleep 60
done

echo "waiting for 10:00 IST decision time..."
while [ "$(TZ=Asia/Kolkata date +%H%M)" -lt 1000 ]; do sleep 30; done

python3 -m app.cli --live --top 3
