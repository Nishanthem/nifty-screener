"""Score the live NSE snapshot with the same composite logic as the backtest.

Components come from the live snapshot (open, last, day VWAP, volume, gap)
plus yfinance daily bars for ATR% and 20-day average volume, which are
slow-moving and don't need to be real-time.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .constituents import NIFTY50
from .data import YFinanceProvider
from .nse_live import (fetch_snapshots, persist, opening_range_high, opening_range_low,
                       candles_from_snapshots, scan_breakout, Snapshot, Breakout)
from .signals import (Score, _atr_pct, composite, levels, MIN_ATR_PCT, MAX_ATR_PCT, LONG, SHORT)
from .wicks import long_wicks, Wick

INDEX_SYMBOL = "NIFTY 50"


def _score_snapshot(s: Snapshot, daily: pd.DataFrame, orh: float | None, orl: float | None,
                    side: str = LONG, breakout: Breakout | None = None,
                    confirm_orb: bool = False) -> Score | None:
    if daily.empty:
        return None
    atr_pct = _atr_pct(daily)
    if not np.isfinite(atr_pct) or not (MIN_ATR_PCT <= atr_pct <= MAX_ATR_PCT):
        return None

    gap_pct = (s.open - s.prev_close) / s.prev_close
    roc = (s.last - s.open) / s.open
    avg_vol = float(daily["Volume"].tail(20).mean())
    rel_vol = s.volume / max(avg_vol, 1)
    # Without polled opening-range data, fall back to the day high/low as a proxy.
    polled = orh is not None and orl is not None
    orh_eff = orh if orh is not None else s.high
    orl_eff = orl if orl is not None else s.low

    confirmed = None
    if confirm_orb:
        confirmed = breakout is not None and breakout.side == side
        if not confirmed:
            return None
    score, comps, risk = composite(side, gap_pct, s.last, orh_eff, orl_eff, s.vwap, roc, rel_vol,
                                   atr_pct, confirmed=confirmed)
    comps["or_source"] = "polled" if polled else "day_range"
    if breakout is not None:
        comps["orb_5m"] = f"{breakout.side} @ {breakout.close_at} close {breakout.close}"
    stop, target = levels(side, s.last, risk)
    return Score(
        ticker=s.symbol, score=round(score, 4), components=comps,
        price=round(s.last, 2), vwap=round(s.vwap, 2), orh=round(orh_eff, 2), orl=round(orl_eff, 2),
        stop=stop, target=target, side=side,
    )


def rank_live(save: bool = True, side: str = "auto",
              confirm_orb: bool = False) -> tuple[list[Score], str]:
    """Returns (ranked picks, side traded).

    `side` is LONG, SHORT or "auto" (follow the index: above VWAP -> longs,
    below -> shorts). Forcing a side against the index gate returns [].

    `confirm_orb` keeps only stocks where a 5-min candle closing at
    09:35/09:40/09:45 closed beyond the 15-min opening range on the traded side.
    """
    snaps = fetch_snapshots()
    if save:
        persist(snaps)

    by_sym = {s.symbol: s for s in snaps}
    index = by_sym.get(INDEX_SYMBOL)
    idx_side = None if index is None else (LONG if index.last > index.vwap else SHORT)
    if side == "auto":
        side = idx_side or LONG
    elif idx_side is not None and idx_side != side:
        return [], idx_side

    provider = YFinanceProvider()
    today = pd.Timestamp.now(tz="Asia/Kolkata")
    out = []
    for t in NIFTY50:
        s = by_sym.get(t)
        if s is None:
            continue
        orh, orl = opening_range_high(today, t), opening_range_low(today, t)
        bo = scan_breakout(today, t, orh, orl) if orh is not None and orl is not None else None
        sc = _score_snapshot(s, provider.daily(t), orh, orl, side, bo, confirm_orb)
        if sc is not None:
            out.append(sc)
    return sorted(out, key=lambda x: x.score, reverse=True), side


def all_breakouts(date: pd.Timestamp | None = None) -> list[tuple[str, float, float, Breakout]]:
    """Every Nifty 50 stock with a confirmed 5-min close outside its 15-min
    opening range today, both directions, ignoring the index gate.
    Returns (symbol, orh, orl, breakout) sorted by confirmation time."""
    date = date or pd.Timestamp.now(tz="Asia/Kolkata")
    out = []
    for t in NIFTY50:
        orh, orl = opening_range_high(date, t), opening_range_low(date, t)
        if orh is None or orl is None:
            continue
        bo = scan_breakout(date, t, orh, orl)
        if bo is not None:
            out.append((t, orh, orl, bo))
    return sorted(out, key=lambda x: (x[3].close_at, x[0]))


def live_wicks(symbol: str, date: pd.Timestamp | None = None) -> list[Wick]:
    """Long wicks in the first three 15-min candles, built from today's polled snapshots."""
    date = date or pd.Timestamp.now(tz="Asia/Kolkata")
    return long_wicks(candles_from_snapshots(date, symbol))
