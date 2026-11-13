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

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .constituents import NIFTY50
from .reversal import (Setup, scan_frame, best_per_day, outcome, to_frame, first_touch,
                       DECISION, BULL)

IST = "Asia/Kolkata"
GAP_FILL_PCT = 0.3  # opened at least this far from prev close, then traded back to it


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


def _official_closes(tickers, date: pd.Timestamp) -> dict[str, pd.Series]:
    """Unadjusted daily closes (the exchange close) for previous-close checks."""
    import yfinance as yf
    from .constituents import yf_symbol
    days = max(10, (pd.Timestamp.now(tz=IST).normalize() - date.normalize()).days + 10)
    df = yf.download([yf_symbol(t) for t in tickers], period=f"{days}d", interval="1d",
                     progress=False, auto_adjust=False, group_by="ticker")
    out = {}
    for t in tickers:
        try:
            out[t] = df[yf_symbol(t)]["Close"].dropna()
        except KeyError:
            out[t] = pd.Series(dtype=float)
    return out


def _prev_close(official: pd.Series, date: pd.Timestamp) -> float | None:
    prior = official[[pd.Timestamp(i).date() < date.date() for i in official.index]]
    return float(prior.iloc[-1]) if not prior.empty else None


def window_start(decision: str) -> str:
    """Each scan covers the three 5m candles before `decision` (09:30 -> 09:15, 09:45 -> 09:30)."""
    return (pd.Timestamp(f"2000-01-01 {decision}") - pd.Timedelta(minutes=15)).strftime("%H:%M")


@dataclass
class ScanResult:
    setups: list[Setup]
    # ticker -> (first candle touching prev close, prev close, open gap %, "crossed up/down" | "touched")
    pc_touches: dict[str, tuple[str, float, float, str]]
    start: str = "09:15"
    decision: str = DECISION
    missing: list[str] = None  # tickers whose last needed candle wasn't available


def _side(px: float, level: float) -> int:
    return 0 if abs(px - level) <= level * 0.0002 else (1 if px > level else -1)


def _pc_cross(day: pd.DataFrame, pc: float, start: str, decision: str) -> str:
    """Did price end the window on the other side of the previous close than it began?"""
    t = day.index.strftime("%H:%M")
    win, before = day[(t >= start) & (t < decision)], day[t < start]
    ref = float(before["Close"].iloc[-1]) if not before.empty else float(day["Open"].iloc[0])
    a, b = _side(ref, pc), _side(float(win["Close"].iloc[-1]), pc)
    if a and b and a != b:
        return "CROSSED UP" if b > 0 else "CROSSED DOWN"
    return "touched"


def _scan(frames: dict[str, pd.DataFrame], closes: dict[str, float | pd.Series], date: pd.Timestamp,
          decision: str, start: str = "09:15") -> ScanResult:
    setups, touches, missing = [], {}, []
    last_slot = (pd.Timestamp(f"2000-01-01 {decision}") - pd.Timedelta(minutes=5)).strftime("%H:%M")
    for t, df in frames.items():
        day = df[df.index.date == date.date()]
        if last_slot not in set(day.index.strftime("%H:%M")):
            missing.append(t)
        c = closes.get(t)
        series = c if isinstance(c, pd.Series) else None
        setups += scan_frame(t, df, {date.date()}, decision, official_close=series, start=start)
        pc = _prev_close(c, date) if series is not None else c
        if pc and not day.empty:
            when = first_touch(day, date, pc, decision, start)
            if when:
                gap = (float(day["Open"].iloc[0]) - pc) / pc * 100
                touches[t] = (when, round(pc, 2), round(gap, 2), _pc_cross(day, pc, start, decision))
    return ScanResult(rank(setups), touches, start, decision, sorted(missing))


def scan_delayed(date: pd.Timestamp, decision: str = DECISION, tickers=NIFTY50) -> ScanResult:
    """yfinance 5m bars (exchange OHLC; any of the last ~60 days)."""
    frames = {t: _history(t, date) for t in tickers}
    return _scan(frames, _official_closes(tickers, date), date, decision, window_start(decision))


def scan_live(date: pd.Timestamp | None = None, decision: str = DECISION, tickers=NIFTY50) -> ScanResult:
    """Today's 5m candles rebuilt from polled NSE snapshots (run `--poll` from 09:15),
    stitched onto yfinance history for RSI warm-up and previous-day levels."""
    from .nse_live import candles_from_snapshots, load_snapshots
    date = date or pd.Timestamp.now(tz=IST)
    snaps = load_snapshots(date)
    prev_close = (snaps[snaps["prev_close"] > 0].groupby("symbol")["prev_close"].first().to_dict()
                  if not snaps.empty else {})
    frames, closes = {}, {}
    for t in tickers:
        today = candles_from_snapshots(date, t, minutes=5)
        if today.empty:
            continue
        today = today.assign(Volume=np.nan)
        today.index = pd.DatetimeIndex(today.index).tz_convert(IST)
        hist = _history(t, date)
        frames[t] = pd.concat([hist[hist.index.date < date.date()], today])
        if t in prev_close:
            closes[t] = pd.Series([prev_close[t]], index=[(date - pd.Timedelta(days=1)).date()])
    return _scan(frames, closes, date, decision, window_start(decision))


def report(res: ScanResult, date: pd.Timestamp, source: str = "") -> str:
    head = (f"Reversal scan (long-tail 5m candles {res.start}-{res.decision}) — {date.date()}"
            f"{f' ({source})' if source else ''}")
    lines = [head]
    if res.missing:
        lines.append(f"  WARNING: {len(res.missing)} stocks missing the last candle: {', '.join(res.missing[:10])}")
    if not res.setups:
        lines.append("  no long-tail candles")
    else:
        lines.append(f"  {'grade':<5} {'side':<4} {'stock':<11} {'candle':<6} {'tail%':>5} {'move%':>6} "
                     f"{'RSI':>5} {'PDL/PDH':>7} {'prevC':>9} {'relvol':>6} {'vwap':>4}  entry→stop")
        for s in res.setups:
            lvl = "test" if s.flags["level"] else f"{s.level_pct:+.2f}"
            pc = "TOUCHED" if s.pc_touch else "-"
            rv = "-" if not np.isfinite(s.rel_vol) else f"{s.rel_vol:.1f}"
            lines.append(f"  {grade(s):<5} {'BUY' if s.side == BULL else 'SELL':<4} {s.ticker:<11} {s.time:<6} "
                         f"{s.wick_pct:>5.2f} {s.move_pct:>+6.2f} {s.rsi:>5.1f} {lvl:>7} {pc:>9} {rv:>6} "
                         f"{'ok' if s.vwap_ok else '-':>4}  {s.entry}→{s.stop}")
    tails = {s.ticker for s in res.setups}
    lines.append("")
    opening = res.start == "09:15"
    lines.append(f"Touched/crossed previous day's close {res.start}-{res.decision}"
                 f"{' (gap fills first)' if opening else ' (crosses first)'}:")
    if not res.pc_touches:
        lines.append("  none")
    if opening:
        key = lambda kv: (abs(kv[1][2]) < GAP_FILL_PCT, kv[1][0], kv[0])
    else:
        key = lambda kv: (kv[1][3] == "touched", kv[1][0], kv[0])
    for t, (when, pc, gap, cross) in sorted(res.pc_touches.items(), key=key):
        kind = ("GAP FILL" if abs(gap) >= GAP_FILL_PCT else "opened near it") if opening else ""
        lines.append(f"  {t:<11} {when}  prev close {pc:<9} open gap {gap:+.2f}%  {cross:<12} {kind:<14}"
                     f"{'  <- also a long-tail setup' if t in tails else ''}")
    lines.append("")
    lines.append("  A = RSI extreme + prev-day level test, B = one of them, C = tail only. "
                 "prevC TOUCHED = the tail candle's range touched the previous day's close. Not investment advice.")
    return "\n".join(lines)


def backtest(tickers=NIFTY50) -> pd.DataFrame:
    """Every graded setup over the ~60 days yfinance serves at 5m, with trade outcomes."""
    from .data import YFinanceProvider
    p = YFinanceProvider()
    closes = _official_closes(tickers, pd.Timestamp.now(tz=IST) - pd.Timedelta(days=90))
    rows = []
    for t in tickers:
        df = p.intraday(t, days=60, interval="5m")
        days = {d: b for d, b in df.groupby(df.index.date)}
        for s in best_per_day(scan_frame(t, df, official_close=closes.get(t))):
            o = outcome(s, days[pd.Timestamp(s.date).date()])
            rows.append({**to_frame([s]).iloc[0].to_dict(), "grade": grade(s), **o})
    return pd.DataFrame(rows).dropna(subset=["r"])


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    def row(d):
        return pd.Series({"setups": len(d), "hit_%": round((d.r > 0).mean() * 100, 1),
                          "avg_R": round(d.r.mean(), 3), "eod_%": round(d.ret_eod.mean(), 3)})
    groups = {"all": df, **{f"grade {g}": df[df.grade == g] for g in "ABC"},
              "A+B": df[df.grade < "C"],
              "tail touched prev close": df[df.pc_touch], "tail not at prev close": df[~df.pc_touch]}
    return pd.DataFrame({k: row(v) for k, v in groups.items() if len(v)}).T
