# Nifty Intraday Screener

Daily Nifty 50 screener that suggests the best intraday candidate — long
when the index is above its VWAP, short when below — with entry, stop, and
target, plus a long-wick (tail) check on the first three 15-min candles. Two data paths: **live NSE** (real-time) and
yfinance (~15 min delayed, used for backtesting and daily ATR/volume context).

## Usage

```bash
pip install -r requirements.txt
playwright install chromium        # only needed for the live path

python -m app.cli --live           # LIVE pick, real-time NSE prices
python -m app.cli --poll           # save a live snapshot (run 09:15-09:45)
python -m app.cli --live --scan    # only stocks whose 5-min candle closed beyond the 15-min OR
./run_day.sh                       # full routine: poll from 09:15, 5m ORB scans at 09:35/09:40/09:45
SIDE=short ./run_day.sh            # force a side (still gated on the index)
python -m app.cli --side short     # same for the CLI (auto | long | short)

python -m app.cli                  # delayed yfinance path
python -m app.cli --date 2026-09-11
python -m app.cli --backtest       # validate logic on ~60d of 15m history

uvicorn app.web:app --port 8123    # web UI at http://localhost:8123
```

Cron (IST server), polling the opening range then picking at 10:00:

```
15-59 9  * * 1-5 cd /path/to/nifty-screener && python3 -m app.cli --poll
0     10 * * 1-5 cd /path/to/nifty-screener && python3 -m app.cli --live --top 3
```

## 5-min ORB scan (`--scan`, what `run_day.sh` does)

The opening range is the **first 15-min candle (09:15–09:30)**. After that
we look at **5-min candles** and, at 09:35, 09:40 and 09:45 IST, check the
candle that just closed: a close **above OR-high** is a long breakout, a
close **below OR-low** a short breakout. `--scan` keeps only stocks with such
a confirmed close on the index's side (long if Nifty > VWAP, short if below)
and ranks them with the usual composite; the first confirming candle is
reported as `orb_5m` in the components. The 09:45 scan is the final pick, the
earlier ones are early alerts. Live 5-min candles are rebuilt from the
1/min snapshot poll, so `--poll` must be running from 09:15.

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

1. **Index gate / side** — Nifty above its VWAP → look for longs only;
   below → shorts only (`--side auto`, the default). Backtest showed longs
   only had positive expectancy on bullish-index days, and vice versa.
2. Per stock, a weighted score of: gap continuation (0.3–3% ideal),
   opening-range breakout (price > first-15m high), above-VWAP,
   momentum since open, relative volume. ATR% must be 0.8–6%.
   Shorts use the exact mirror: gap-down, break *below* the first-15m low,
   below VWAP, negative momentum.
3. Entry at decision price, stop beyond min(OR-high, VWAP) for longs /
   max(OR-low, VWAP) for shorts, target 1.5R, square off 15:15.
4. **Wick check** — each pick reports long tails in the 09:15/09:30/09:45
   candles (wick ≥ 2× body and ≥ 50% of range). A long upper wick means
   buyers were rejected at highs (caution on longs); a long lower wick means
   sellers were absorbed (caution on shorts). Live candles are rebuilt from
   the 1/min snapshot poll, so they're approximate; the delayed path uses
   yfinance 15m bars.

## Backtest result (60 days, yfinance 15m, no costs)

| metric | value |
|---|---|
| trades (gated) | 25 |
| hit rate | 48% |
| expectancy | +0.21R/trade |
| index same window | −0.04%/day |

Short side, same window and logic mirrored (re-run Sep 2026, 60d):

| side | trades | hit | expectancy |
|---|---|---|---|
| long only | 22 | 45% | +0.20R |
| short only | 36 | 58% | +0.23R |
| auto (follow index) | 58 | 53% | +0.22R |

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
