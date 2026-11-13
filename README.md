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
python -m app.cli --poll           # save a live snapshot (run 09:15-10:00)
./run_day.sh                       # full routine: poll 09:15-10:00, pick at 10:00
SIDE=short ./run_day.sh            # force a side (still gated on the index)
python -m app.cli --side short     # same for the CLI (auto | long | short)

python -m app.cli                  # delayed yfinance path
python -m app.cli --date 2026-09-11
python -m app.cli --backtest       # validate logic on ~60d of 15m history

python -m app.cli --reversal                  # 09:30 reversal scan (delayed yfinance 5m)
python -m app.cli --reversal --date 2026-11-12
python -m app.cli --reversal --live           # from polled live snapshots
./run_reversal.sh                             # poll 09:15-09:30, scan at 09:30
python -m app.cli --reversal --backtest       # grade stats over ~60d of 5m history

uvicorn app.web:app --port 8123    # web UI at http://localhost:8123
```

Cron (IST server), polling the opening range then picking at 10:00:

```
15-59 9  * * 1-5 cd /path/to/nifty-screener && python3 -m app.cli --poll
0     10 * * 1-5 cd /path/to/nifty-screener && python3 -m app.cli --live --top 3
30    9  * * 1-5 cd /path/to/nifty-screener && python3 -m app.cli --reversal --live
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

## Opening reversal scan (09:30, 5m long tails)

At 09:30 the 09:15/09:20/09:25 five-minute candles are complete. Any Nifty 50
stock whose candle has a long tail (wick ≥ 2× body, ≥ 50% of range, ≥ 0.25%
of price) is a reversal candidate: lower tail → BUY, upper tail → SELL.
Each one is checked against:

| confirmation | rule | kept? |
|---|---|---|
| RSI(14) on 5m | ≤ 35 (bull) / ≥ 65 (bear) at the tail | **yes**: helped in both halves of the window |
| prev-day level | tail tested the previous day's low (bull) / high (bear) | **yes** |
| against the move | tail opposes a ≥ 0.3% move from prev close | shown only, small effect |
| VWAP | 09:25 close back on the reversal side of VWAP | shown only, mixed |
| relative volume | tail candle volume vs. 10-day average for that 5m slot | shown only, didn't help; yfinance often reports 0 volume for 09:15 |

Grade **A** = RSI + level, **B** = one of them, **C** = tail only. The scan
lists every tail, C included. Setups where price at 09:30 has already moved
past the tail's extreme are dropped.

**Previous close**: the `prevC` column marks tails whose candle touched the
previous day's official close. A second list shows every Nifty 50 stock that
touched its previous close between 09:15 and 09:30. **GAP FILL** stocks (opened
≥ 0.3% away, then came back) are listed first, before stocks that simply
opened near the previous close. The previous close is the unadjusted exchange
close (yfinance daily, or NSE's `previousClose` on the live path).
Trade model: enter at 09:30, stop just beyond the tail's extreme, 1.5R
target, square off 15:15.

Backtest (`--reversal --backtest`, 59 days of yfinance 5m to 12 Nov 2026,
one setup per stock-day, no costs):

| group | setups | hit | avg R | EOD move |
|---|---|---|---|---|
| all tails | 811 | 47.7% | +0.11R | +0.17% |
| **grade A** | 204 | 52.5% | **+0.23R** | +0.35% |
| grade B | 207 | 47.8% | +0.15R | +0.25% |
| grade C | 400 | 45.2% | +0.03R | +0.04% |
| control: fade every opening move, no tail filter | 2991 | 42.6% | +0.03R | +0.03% |
| tail touched prev close | 250 | 46.8% | +0.10R | +0.22% |
| tail not at prev close | 561 | 48.1% | +0.11R | +0.15% |

Touching the previous close is neutral on its own, so it's reported but doesn't
change the grade. Tails do carry information compared with blindly fading the open, and RSI plus
the prev-day level roughly double the edge. After ~0.05–0.1R of costs only
grade A clearly stays positive. This is a single 59-day window, so paper-trade it first.

*Not investment advice.*

## 09:45 follow-up scan

`python -m app.cli --reversal --at 09:45` runs the same report for the 09:30/09:35/09:40 candles,
and lists stocks whose range touched the previous close in that window, marking `CROSSED UP/DOWN`
when price ended the window on the other side of it.

## Rolling 5-minute tail watch

`python -m app.cli --watch [--live] [--strong]` checks every 5-min candle as it closes,
09:15-14:55, and prints each new long-tail candle (same tail rule as the 09:30 scan) with tags:
`day_extreme` (new session high/low), `rsi` (RSI(14) <=35 / >=65), `level` (prev-day low/high),
`prev_close` (touched yesterday's close). **STRONG** = `rsi`+`level`, or `day_extreme`+`prev_close`.
`run_watch.sh` runs it all session with live NSE snapshots and logs to `logs/`.
`--watch --date YYYY-MM-DD` replays a past day; `--watch --backtest` prints the stats below.

Backtest, 59 days of 5m data, all Nifty 50 (enter next candle's open, stop beyond tail, 1.5R, exit 15:15, no costs):

| group | tails | hit % | avg R |
|---|---|---|---|
| all | 2187 | 42.6 | +0.04 |
| STRONG | 765 | 44.7 | +0.11 |
| 09:15-09:30 all | 931 | 44.1 | +0.09 |
| 09:15-09:30 STRONG | 448 | 46.7 | +0.16 |
| after 09:30 all | 1256 | 41.4 | +0.00 |
| after 09:30 STRONG | 317 | 42.0 | +0.03 |

Tails after 09:30 have essentially no edge, even when STRONG; the 10:00-11:00 hour was negative.
Treat later alerts as watch-list context rather than trades.
