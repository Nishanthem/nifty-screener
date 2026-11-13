"""Daily suggestion CLI.

    python -m app.cli --live     # live NSE data (real-time, via browser session)
    python -m app.cli            # delayed yfinance data
    python -m app.cli --top 5
    python -m app.cli --poll     # snapshot now (run 09:15-09:30 to fix the opening range)
    python -m app.cli --backtest # validate on ~60d of intraday history
    python -m app.cli --side short   # force shorts (still gated on index below VWAP)
    python -m app.cli --side auto    # default: longs if index > VWAP, shorts if below
    python -m app.cli --reversal             # 09:30 scan: long-tailed 5m reversal candles
    python -m app.cli --reversal --live      # same, from polled live snapshots
    python -m app.cli --reversal --backtest  # grade stats over ~60d of 5m history
    python -m app.cli --watch [--live] [--strong]  # every 5m candle, all day, as it closes
    python -m app.cli --watch --backtest     # rolling-scan stats over ~60d
"""

from __future__ import annotations

import argparse
import datetime as dt

import pandas as pd

from .data import YFinanceProvider, load_all
from .signals import rank, Score, LONG, SHORT
from .wicks import long_wicks, describe, day_candles
from . import backtest

IST = pd.Timestamp.now(tz="Asia/Kolkata")


def _print_pick(s: Score, wick_note: str) -> None:
    tag = "BUY " if s.side == LONG else "SELL"
    print(f"{tag} {s.ticker:<12} score={s.score:.2f}  px={s.price}  "
          f"stop={s.stop}  target={s.target}  vwap={s.vwap}  orh={s.orh}  orl={s.orl}")
    print(f"{'':<17} {s.components}")
    print(f"{'':<17} wicks: {wick_note}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--date", type=str, default=None, help="YYYY-MM-DD, default today")
    ap.add_argument("--time", type=str, default="10:00", help="decision time IST")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--live", action="store_true", help="use live NSE data")
    ap.add_argument("--poll", action="store_true", help="persist a live snapshot and exit")
    ap.add_argument("--side", choices=["auto", LONG, SHORT], default="auto",
                    help="auto follows the index: longs above VWAP, shorts below")
    ap.add_argument("--reversal", action="store_true",
                    help="09:30 scan for long-tailed 5m reversal candles + RSI/level confirmations")
    ap.add_argument("--watch", action="store_true",
                    help="rolling scan: report each new long-tail 5m candle as it closes, until 15:00")
    ap.add_argument("--strong", action="store_true", help="with --watch: only STRONG tails")
    args = ap.parse_args()

    if args.watch:
        from . import rolling
        if args.backtest:
            df = rolling.backtest()
            print(f"{len(df)} tails over {df.date.nunique()} days (enter next candle open, "
                  f"stop beyond tail, 1.5R, exit 15:15, no costs)\n")
            print(rolling.summarize(df).to_string())
            return
        if args.date:
            d = pd.Timestamp(args.date, tz="Asia/Kolkata")
            from .constituents import NIFTY50
            from .data import YFinanceProvider
            from .wick_scan import _official_closes
            closes = _official_closes(NIFTY50, d)
            p = YFinanceProvider(cache=False)
            tails = [t for tk in NIFTY50 for t in rolling.scan_frame(
                tk, p.intraday(tk, days=60, interval="5m"), {d.date()}, closes.get(tk))]
            for t in sorted(tails, key=lambda x: (x.time, x.ticker)):
                if t.strong or not args.strong:
                    print(rolling.fmt(t))
            return
        rolling.watch(live=args.live, strong_only=args.strong)
        return

    if args.poll:
        from .nse_live import fetch_snapshots, persist
        snaps = fetch_snapshots()
        print(f"saved {len(snaps)} snapshots -> {persist(snaps)}")
        return

    if args.reversal:
        from . import wick_scan
        if args.backtest:
            df = wick_scan.backtest()
            print(f"{len(df)} setups over {df.date.nunique()} days "
                  f"(enter 09:30, stop beyond tail, 1.5R target, exit 15:15, no costs)\n")
            print(wick_scan.summarize(df).to_string())
            return
        date = pd.Timestamp(args.date, tz="Asia/Kolkata") if args.date else IST.normalize()
        hits = wick_scan.scan_live(date) if args.live else wick_scan.scan_delayed(date)
        print(wick_scan.report(hits, date, "live snapshots" if args.live else "yfinance 5m, delayed"))
        return

    if args.live:
        from .live_score import rank_live, live_wicks
        picks, side = rank_live(side=args.side)
        if not picks:
            where = "below" if side == SHORT else "above"
            print(f"Index {where} its VWAP — no {args.side} today (gate).")
            return
        print(f"LIVE — top {args.top} intraday {side.upper()} candidates ({IST:%Y-%m-%d %H:%M} IST)\n")
        for s in picks[: args.top]:
            _print_pick(s, describe(live_wicks(s.ticker)))
        return

    provider = YFinanceProvider()
    index_bars = provider.index_intraday()

    if args.backtest:
        universe = load_all(provider)
        df = backtest.run(universe, index_bars, side=args.side)
        print(backtest.summarize(df))
        print(df.tail(15).to_string(index=False))
        return

    date = pd.Timestamp(args.date, tz="Asia/Kolkata") if args.date else IST.normalize()
    universe = load_all(provider)
    picks = rank(universe, date, index_bars, decision_time=args.time, side=args.side)

    if not picks:
        print(f"No {args.side} candidates passed filters today (index gate or no data).")
        return
    side = picks[0].side
    print(f"Top {args.top} intraday {side.upper()} candidates — {date.date()} @ {args.time} IST (delayed data)\n")
    for s in picks[: args.top]:
        bars = day_candles(universe[s.ticker].intraday, date)
        _print_pick(s, describe(long_wicks(bars)))


if __name__ == "__main__":
    main()
