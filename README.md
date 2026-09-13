# Nifty Intraday Screener

Daily Nifty 50 screener that suggests the best intraday long candidate,
with entry, stop, and target. Two data paths: **live NSE** (real-time) and
yfinance (~15 min delayed, used for backtesting and daily ATR/volume context).

## Usage

```bash
pip install -r requirements.txt
playwright install chromium        # only needed for the live path

python -m app.cli --live           # LIVE pick, real-time NSE prices
python -m app.cli --poll           # save a live snapshot (run 09:15-09:30)
./run_day.sh                       # full routine: poll open range, pick at 10:00

python -m app.cli                  # delayed yfinance path
python -m app.cli --date 2026-09-11
python -m app.cli --backtest       # validate logic on ~60d of 15m history

uvicorn app.web:app --port 8123    # web UI at http://localhost:8123
```

Cron (IST server), polling the opening range then picking at 10:00:

```
15-30 9  * * 1-5 cd /path/to/nifty-screener && python3 -m app.cli --poll
0     10 * * 1-5 cd /path/to/nifty-screener && python3 -m app.cli --live --top 3
```

## Live data notes

NSE's JSON endpoints (`/api/NextApi/...`) reject datacenter IPs directly
(403 + Akamai bot challenge), so `app/nse_live.py` drives a real Chrome over
CDP (`localhost:29229`) and fetches from the page context. This works, but:

- the endpoints are **undocumented** and can change without notice
- they return *snapshots*, not bars — day VWAP is derived exactly as
  `totalTradedValue / totalTradedVolume`, and the opening-range high is built
  by polling from 09:15, which is why `--poll` matters
- poll politely (60s is plenty); hammering will get the IP blocked

A broker API (Zerodha Kite / Angel One) remains the robust option — same
interface, just implement the provider methods in `app/data.py`.

## The logic

At the decision time (default 10:00 IST, after opening noise):

1. **Index gate** — no pick unless Nifty itself trades above its VWAP.
   Backtest showed longs only had positive expectancy on such days.
2. Per stock, a weighted score of: gap continuation (0.3–3% ideal),
   opening-range breakout (price > first-15m high), above-VWAP,
   momentum since open, relative volume. ATR% must be 0.8–6%.
3. Entry at decision price, stop below min(OR-high, VWAP), target 1.5R,
   square off 15:15.

## Backtest result (60 days, yfinance 15m, no costs)

| metric | value |
|---|---|
| trades (gated) | 25 |
| hit rate | 48% |
| expectancy | +0.21R/trade |
| index same window | −0.04%/day |

Parameter sweep on the same 60d window (why 15m/10:00):

| variant | trades | hit | expectancy |
|---|---|---|---|
| **15m OR, decide 10:00 (chosen)** | 25-32 | 48-50% | **+0.21 to +0.23R** |
| 30m OR, 10:00 | 25-32 | 56% | +0.13R |
| 15m OR, decide 9:45 | 32-37 | 38-44% | −0.14 to +0.01R |
| 5m OR, 9:45 | 37 | 35% | −0.17R |

Earlier decisions and shorter ranges are strictly worse — the opening
noise hasn't resolved by 9:45, and a 5-min range whipsaws.

**Honest caveat:** 25 trades is a small sample and excludes
brokerage/slippage (~0.05–0.1R/trade). Promising, not proven —
paper-trade it before sizing.

*Not investment advice.*
