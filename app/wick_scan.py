"""09:30 reversal scan: which stocks printed a long-tailed 5-min candle?

At 09:30 IST the 09:15/09:20/09:25 candles are complete. Every stock is
checked for a long tail (see `reversal`), then graded on the confirmations
that held up in the 60-day backtest:

    A  RSI(14, 5m) extreme AND tail tested the previous day's low/high
    B  one of the two
    C  tail only

Volume and VWAP are shown for context but don't change the grade (neither
improved the backtest).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .constituents import NIFTY50
from .reversal import Setup, scan_frame, best_per_day, outcome, to_frame, DECISION, BULL

IST = "Asia/Kolkata"


def grade(s: Setup) -> str:
    n = s.flags["rsi"] + s.flags["level"]
    return "ABC"[2 - n]


def still_valid(s: Setup) -> bool:
    """Price at 09:30 hasn't already run through the tail extreme (stop)."""
    return s.entry > s.stop if s.side == BULL else s.entry < s.stop


def rank(setups: list[Setup]) -> list[Setup]:
    return sorted((s for s in best_per_day(setups) if still_valid(s)),
                  key=lambda s: (grade(s), -s.wick_pct))


def _history(ticker: str, date: pd.Timestamp) -> pd.DataFrame:
    from .data import YFinanceProvider
    recent = (pd.Timestamp.now(tz=IST).normalize() - date.normalize()).days <= 5
    p = YFinanceProvider(cache=not recent)
    return p.intraday(ticker, days=7 if recent else 60, interval="5m")


def scan_delayed(date: pd.Timestamp, decision: str = DECISION, tickers=NIFTY50) -> list[Setup]:
    """yfinance 5m bars (exchange OHLC, ~15 min delayed; any of the last ~60 days)."""
    out = []
    for t in tickers:
        out += scan_frame(t, _history(t, date), {date.date()}, decision)
    return rank(out)


def scan_live(date: pd.Timestamp | None = None, decision: str = DECISION, tickers=NIFTY50) -> list[Setup]:
    """Today's 5m candles rebuilt from polled NSE snapshots (run `--poll` from 09:15),
    stitched onto yfinance history for RSI warm-up and previous-day levels."""
    from .nse_live import candles_from_snapshots
    date = date or pd.Timestamp.now(tz=IST)
    out = []
    for t in tickers:
        today = candles_from_snapshots(date, t, minutes=5)
        if today.empty:
            continue
        today = today.assign(Volume=np.nan)
        today.index = pd.DatetimeIndex(today.index).tz_convert(IST)
        hist = _history(t, date)
        hist = hist[hist.index.date < date.date()]
        out += scan_frame(t, pd.concat([hist, today]), {date.date()}, decision)
    return rank(out)


def report(setups: list[Setup], date: pd.Timestamp, source: str = "") -> str:
    head = (f"Opening reversal scan (long-tail 5m candles 09:15-{DECISION}) — {date.date()}"
            f"{f' ({source})' if source else ''}")
    if not setups:
        return f"{head}\n  none"
    lines = [head, f"  {'grade':<5} {'side':<4} {'stock':<11} {'candle':<6} {'tail%':>5} {'move%':>6} "
                   f"{'RSI':>5} {'PDL/PDH':>7} {'relvol':>6} {'vwap':>4}  entry→stop"]
    for s in setups:
        lvl = "test" if s.flags["level"] else f"{s.level_pct:+.2f}"
        rv = "-" if not np.isfinite(s.rel_vol) else f"{s.rel_vol:.1f}"
        lines.append(f"  {grade(s):<5} {'BUY' if s.side == BULL else 'SELL':<4} {s.ticker:<11} {s.time:<6} "
                     f"{s.wick_pct:>5.2f} {s.move_pct:>+6.2f} {s.rsi:>5.1f} {lvl:>7} {rv:>6} "
                     f"{'ok' if s.vwap_ok else '-':>4}  {s.entry}→{s.stop}")
    lines.append("  A = RSI extreme + prev-day level test, B = one of them, C = tail only. Not investment advice.")
    return "\n".join(lines)


def backtest(tickers=NIFTY50) -> pd.DataFrame:
    """Every graded setup over the ~60 days yfinance serves at 5m, with trade outcomes."""
    from .data import YFinanceProvider
    p = YFinanceProvider()
    rows = []
    for t in tickers:
        df = p.intraday(t, days=60, interval="5m")
        days = {d: b for d, b in df.groupby(df.index.date)}
        for s in best_per_day(scan_frame(t, df)):
            o = outcome(s, days[pd.Timestamp(s.date).date()])
            rows.append({**to_frame([s]).iloc[0].to_dict(), "grade": grade(s), **o})
    return pd.DataFrame(rows).dropna(subset=["r"])


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    def row(d):
        return pd.Series({"setups": len(d), "hit_%": round((d.r > 0).mean() * 100, 1),
                          "avg_R": round(d.r.mean(), 3), "eod_%": round(d.ret_eod.mean(), 3)})
    groups = {"all": df, **{f"grade {g}": df[df.grade == g] for g in "ABC"},
              "A+B": df[df.grade < "C"]}
    return pd.DataFrame({k: row(v) for k, v in groups.items() if len(v)}).T
