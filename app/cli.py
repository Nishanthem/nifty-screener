"""Daily suggestion CLI.

    python -m app.cli --live     # live NSE data (real-time, via browser session)
    python -m app.cli            # delayed yfinance data
    python -m app.cli --top 5
    python -m app.cli --poll     # snapshot now (run 09:15-09:30 to fix the opening range)
    python -m app.cli --backtest # validate on ~60d of intraday history
"""

from __future__ import annotations

import argparse
import datetime as dt

import pandas as pd

from .data import YFinanceProvider, load_all
from .signals import rank
from . import backtest

IST = pd.Timestamp.now(tz="Asia/Kolkata")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--date", type=str, default=None, help="YYYY-MM-DD, default today")
    ap.add_argument("--time", type=str, default="10:00", help="decision time IST")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--live", action="store_true", help="use live NSE data")
    ap.add_argument("--poll", action="store_true", help="persist a live snapshot and exit")
    args = ap.parse_args()

    if args.poll:
        from .nse_live import fetch_snapshots, persist
        snaps = fetch_snapshots()
        print(f"saved {len(snaps)} snapshots -> {persist(snaps)}")
        return

    if args.live:
        from .live_score import rank_live
        picks, bullish = rank_live()
        if not bullish:
            print("Index below its VWAP — no long today (gate).")
            return
        print(f"LIVE — top {args.top} intraday candidates ({IST:%Y-%m-%d %H:%M} IST)\n")
        for s in picks[: args.top]:
            print(f"{s.ticker:<14} score={s.score:.2f}  px={s.price}  "
                  f"stop={s.stop}  target={s.target}  vwap={s.vwap}  orh={s.orh}")
            print(f"{'':<14} {s.components}")
        return

    provider = YFinanceProvider()
    index_bars = provider.index_intraday()

    if args.backtest:
        universe = load_all(provider)
        df = backtest.run(universe, index_bars)
        print(backtest.summarize(df))
        print(df.tail(15).to_string(index=False))
        return

    date = pd.Timestamp(args.date, tz="Asia/Kolkata") if args.date else IST.normalize()
    universe = load_all(provider)
    picks = rank(universe, date, index_bars, decision_time=args.time)

    if not picks:
        print("No candidates passed filters today.")
        return
    print(f"Top {args.top} intraday candidates — {date.date()} @ {args.time} IST\n")
    for s in picks[: args.top]:
        print(f"{s.ticker:<14} score={s.score:.2f}  px={s.price}  "
              f"stop={s.stop}  target={s.target}  vwap={s.vwap}  orh={s.orh}")
        print(f"{'':<14} {s.components}")


if __name__ == "__main__":
    main()
