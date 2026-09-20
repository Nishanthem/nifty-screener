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
                       candles_from_snapshots, Snapshot)
from .signals import (Score, _atr_pct, composite, levels, MIN_ATR_PCT, MAX_ATR_PCT, LONG, SHORT)
from .wicks import long_wicks, Wick

INDEX_SYMBOL = "NIFTY 50"


def _score_snapshot(s: Snapshot, daily: pd.DataFrame, orh: float | None, orl: float | None,
                    side: str = LONG) -> Score | None:
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

    score, comps, risk = composite(side, gap_pct, s.last, orh_eff, orl_eff, s.vwap, roc, rel_vol, atr_pct)
    comps["or_source"] = "polled" if polled else "day_range"
    stop, target = levels(side, s.last, risk)
    return Score(
        ticker=s.symbol, score=round(score, 4), components=comps,
        price=round(s.last, 2), vwap=round(s.vwap, 2), orh=round(orh_eff, 2), orl=round(orl_eff, 2),
        stop=stop, target=target, side=side,
    )


def rank_live(save: bool = True, side: str = "auto") -> tuple[list[Score], str]:
    """Returns (ranked picks, side traded).

    `side` is LONG, SHORT or "auto" (follow the index: above VWAP -> longs,
    below -> shorts). Forcing a side against the index gate returns [].
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
        sc = _score_snapshot(s, provider.daily(t), opening_range_high(today, t),
                             opening_range_low(today, t), side)
        if sc is not None:
            out.append(sc)
    return sorted(out, key=lambda x: x.score, reverse=True), side


def live_wicks(symbol: str, date: pd.Timestamp | None = None) -> list[Wick]:
    """Long wicks in the first three 15-min candles, built from today's polled snapshots."""
    date = date or pd.Timestamp.now(tz="Asia/Kolkata")
    return long_wicks(candles_from_snapshots(date, symbol))
