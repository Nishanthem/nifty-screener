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
from .nse_live import fetch_snapshots, persist, opening_range_high, Snapshot
from .signals import (Score, _atr_pct, _clip01, MIN_ATR_PCT, MAX_ATR_PCT)

INDEX_SYMBOL = "NIFTY 50"


def _score_snapshot(s: Snapshot, daily: pd.DataFrame, orh: float | None) -> Score | None:
    if daily.empty:
        return None
    atr_pct = _atr_pct(daily)
    if not np.isfinite(atr_pct) or not (MIN_ATR_PCT <= atr_pct <= MAX_ATR_PCT):
        return None

    gap_pct = (s.open - s.prev_close) / s.prev_close
    roc = (s.last - s.open) / s.open
    avg_vol = float(daily["Volume"].tail(20).mean())
    rel_vol = s.volume / max(avg_vol, 1)
    # Without polled opening-range data, fall back to the day high as a proxy.
    orh_eff = orh if orh is not None else s.high

    c_gap = _clip01(gap_pct, 0.003, 0.03) - _clip01(gap_pct, 0.05, 0.10) * 0.5
    c_orb = 1.0 if s.last > orh_eff else 0.4 * _clip01(s.last / orh_eff, 0.99, 1.0)
    c_vwap = 1.0 if s.last > s.vwap else 0.3 * _clip01(s.last / s.vwap, 0.995, 1.0)
    c_roc = _clip01(roc, 0.0, 0.02)
    c_vol = _clip01(rel_vol, 0.05, 0.30)

    score = 0.25 * c_gap + 0.25 * c_orb + 0.20 * c_vwap + 0.15 * c_roc + 0.15 * c_vol
    risk = max(s.last - min(orh_eff, s.vwap), s.last * atr_pct * 0.5)

    return Score(
        ticker=s.symbol, score=round(score, 4),
        components={"gap_pct": round(gap_pct * 100, 2), "orb": round(c_orb, 2),
                    "vwap_above": bool(s.last > s.vwap), "roc_pct": round(roc * 100, 2),
                    "rel_vol": round(rel_vol, 2), "atr_pct": round(atr_pct * 100, 2),
                    "orh_source": "polled" if orh is not None else "day_high"},
        price=round(s.last, 2), vwap=round(s.vwap, 2), orh=round(orh_eff, 2),
        stop=round(s.last - risk, 2), target=round(s.last + 1.5 * risk, 2),
    )


def rank_live(save: bool = True) -> tuple[list[Score], bool]:
    """Returns (ranked picks, index_bullish). Empty list when the index gate fails."""
    snaps = fetch_snapshots()
    if save:
        persist(snaps)

    by_sym = {s.symbol: s for s in snaps}
    index = by_sym.get(INDEX_SYMBOL)
    bullish = True if index is None else index.last > index.vwap
    if not bullish:
        return [], False

    provider = YFinanceProvider()
    today = pd.Timestamp.now(tz="Asia/Kolkata")
    out = []
    for t in NIFTY50:
        s = by_sym.get(t)
        if s is None:
            continue
        sc = _score_snapshot(s, provider.daily(t), opening_range_high(today, t))
        if sc is not None:
            out.append(sc)
    return sorted(out, key=lambda x: x.score, reverse=True), True
