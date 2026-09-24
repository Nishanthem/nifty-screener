"""Daily suggestion CLI.

    python -m app.cli --live     # live NSE data (real-time, via browser session)
    python -m app.cli            # delayed yfinance data
    python -m app.cli --top 5
    python -m app.cli --poll     # snapshot now (run 09:15-09:45 to fix the opening range + 5m candles)
    python -m app.cli --live --scan  # only stocks whose 5m candle (09:35/40/45 close) broke the 15m OR
    python -m app.cli --breakouts    # list every stock with a 5m close above/below its OR (no gate, no fetch)
    python -m app.cli --backtest # validate on ~60d of intraday history
    python -m app.cli --side short   # force shorts (still gated: blocked when Nifty >= +0.3%)
    python -m app.cli --side auto    # default: Nifty >= +0.3% vs prev close -> longs only,
                                     #          <= -0.3% -> shorts only, in between -> both
"""

from __future__ import annotations

import argparse
import datetime as dt

import pandas as pd

from .data import YFinanceProvider, load_all
from .signals import rank, Score, LONG, SHORT, BOTH, index_change, index_regime
from .wicks import long_wicks, describe, day_candles
from . import backtest

IST = pd.Timestamp.now(tz="Asia/Kolkata")


def _print_pick(s: Score, wick_note: str) -> None:
    tag = "BUY " if s.side == LONG else "SELL"
    print(f"{tag} {s.ticker:<12} score={s.score:.2f}  px={s.price}  "
          f"stop={s.stop}  target={s.target}  vwap={s.vwap}  orh={s.orh}  orl={s.orl}")
    print(f"{'':<17} {s.components}")
    print(f"{'':<17} wicks: {wick_note}")


def _gate_note(regime: str | None, chg: float | None) -> str:
    if regime is None or chg is None:
        return "Index gate: no index data — both sides allowed"
    what = {LONG: "LONGS (buy) only", SHORT: "SHORTS (sell) only", BOTH: "both buy and sell allowed"}[regime]
    return f"Index gate: Nifty {chg * 100:+.2f}% vs prev close -> {what}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--date", type=str, default=None, help="YYYY-MM-DD, default today")
    ap.add_argument("--time", type=str, default="10:00", help="decision time IST")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--live", action="store_true", help="use live NSE data")
    ap.add_argument("--poll", action="store_true", help="persist a live snapshot and exit")
    ap.add_argument("--scan", action="store_true",
                    help="live: require a 5-min candle close (09:35/09:40/09:45) beyond the 15-min opening range")
    ap.add_argument("--breakouts", action="store_true",
                    help="list all 5-min opening-range breakouts (both sides) from today's snapshots and exit")
    ap.add_argument("--side", choices=["auto", LONG, SHORT], default="auto",
                    help="auto follows the index vs prev close: >=+0.3%% longs, <=-0.3%% shorts, else both")
    args = ap.parse_args()

    if args.poll:
        from .nse_live import fetch_snapshots, persist
        snaps = fetch_snapshots()
        print(f"saved {len(snaps)} snapshots -> {persist(snaps)}")
        return

    if args.breakouts:
        from .live_score import all_breakouts
        rows = all_breakouts()
        print(f"5m ORB breakouts vs 15m opening range ({IST:%Y-%m-%d %H:%M} IST): {len(rows)}")
        for sym, orh, orl, b in rows:
            arrow = "ABOVE" if b.side == LONG else "BELOW"
            print(f"  {sym:<12} {arrow} OR  close={b.close} @ {b.close_at}  orh={orh}  orl={orl}")
        return

    if args.live:
        from .live_score import rank_live, live_wicks
        picks, regime, chg = rank_live(side=args.side, confirm_orb=args.scan)
        gate = _gate_note(regime, chg)
        if not picks:
            if args.side != "auto" and regime not in (None, BOTH, args.side):
                print(f"{gate} — no {args.side} today (gate).")
            elif args.scan:
                print(f"{gate} — no 5-min ORB confirmation yet at {IST:%H:%M} IST.")
            else:
                print(f"{gate} — no candidates passed filters today.")
            return
        mode = "5m ORB scan" if args.scan else "LIVE"
        print(f"{mode} — top {args.top} intraday candidates ({IST:%Y-%m-%d %H:%M} IST)\n{gate}\n")
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

    chg = index_change(index_bars, date, args.time) if not index_bars.empty else None
    gate = _gate_note(None if chg is None else index_regime(chg), chg)
    if not picks:
        print(f"{gate} — no {args.side} candidates passed filters today (index gate or no data).")
        return
    print(f"Top {args.top} intraday candidates — {date.date()} @ {args.time} IST (delayed data)\n{gate}\n")
    for s in picks[: args.top]:
        bars = day_candles(universe[s.ticker].intraday, date)
        _print_pick(s, describe(long_wicks(bars)))


if __name__ == "__main__":
    main()
