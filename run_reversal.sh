#!/usr/bin/env bash
# Reversal scan from live NSE data: ./run_reversal.sh [09:30|09:45]
#   - polls NSE snapshots every 15s through the scan window (the 3 candles before AT)
#   - at AT prints long-tail 5m candles + prev-close touches/crosses
#   - falls back to yfinance bars if the live scan fails
# Requires Chrome running with CDP on localhost:29229 (NSE blocks raw requests).
set -uo pipefail
cd "$(dirname "$0")"
AT="${1:-09:30}"
UNTIL="$(TZ=Asia/Kolkata date -d "today $AT 15 seconds" +%H:%M:%S)"

echo "polling NSE snapshots until $UNTIL IST..."
python3 -m app.cli --poll-until "${UNTIL:0:5}" --every "${POLL_SECS:-15}"
while [ "$(TZ=Asia/Kolkata date +%H%M%S)" \< "${UNTIL//:/}" ]; do
  python3 -m app.cli --poll >/dev/null 2>&1 || true
  sleep 5
done

python3 -m app.cli --reversal --live --at "$AT" || {
  echo "live scan failed; falling back to yfinance bars"
  python3 -m app.cli --reversal --at "$AT"
}
