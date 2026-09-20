"""Long-wick (tail) detection on the first few 15-min candles.

A long upper wick in the opening candles = buyers were rejected at highs
(supply overhead); a long lower wick = sellers were absorbed (demand below).
Useful colour on a pick: a long *upper* tail argues against a long, a long
*lower* tail argues against a short.

A wick is "long" when it is at least WICK_BODY_RATIO x the body and at least
WICK_RANGE_SHARE of the candle's full range.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

WICK_BODY_RATIO = 2.0
WICK_RANGE_SHARE = 0.5
FIRST_CANDLES = 3


@dataclass
class Wick:
    time: str        # candle start, HH:MM IST
    side: str        # "upper" | "lower"
    wick_pct: float  # wick length as % of price
    range_share: float


def long_wicks(bars: pd.DataFrame, n: int = FIRST_CANDLES) -> list[Wick]:
    """Long tails among the first `n` candles of an OHLC frame (IST index)."""
    out: list[Wick] = []
    for ts, b in bars.head(n).iterrows():
        o, h, l, c = float(b["Open"]), float(b["High"]), float(b["Low"]), float(b["Close"])
        rng = h - l
        if rng <= 0:
            continue
        body = abs(c - o)
        upper, lower = h - max(o, c), min(o, c) - l
        for side, w in (("upper", upper), ("lower", lower)):
            if w >= WICK_BODY_RATIO * body and w / rng >= WICK_RANGE_SHARE:
                out.append(Wick(time=pd.Timestamp(ts).strftime("%H:%M"), side=side,
                                wick_pct=round(w / c * 100, 2), range_share=round(w / rng, 2)))
    return out


def describe(wicks: list[Wick]) -> str:
    if not wicks:
        return "no long wicks in first 3 candles"
    return "; ".join(f"{w.time} long {w.side} wick ({w.wick_pct}%, {int(w.range_share * 100)}% of range)"
                     for w in wicks)


def day_candles(intraday: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
    """Today's bars from a yfinance-style intraday frame."""
    if intraday.empty:
        return intraday
    return intraday[intraday.index.date == date.date()]
