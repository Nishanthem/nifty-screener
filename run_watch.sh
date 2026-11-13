#!/usr/bin/env bash
# Rolling 5m long-tail watch, 09:15-15:00 IST. Cron (server in UTC): 40 3 * * 1-5
# Needs Chrome with CDP on :29229 for --live; drop --live to use delayed yfinance bars.
cd "$(dirname "$0")"
mkdir -p logs
python3 -m app.cli --watch --live "$@" | tee -a "logs/watch-$(TZ=Asia/Kolkata date +%F).log"
