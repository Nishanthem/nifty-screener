"""Composite intraday scoring.

Idea: at a decision time (default 10:00 IST, after the opening noise settles),
score each Nifty 50 stock on four well-studied intraday edges:

  1. Gap continuation   — moderate gaps (0.3-3%) tend to continue, large gaps fade
  2. Opening range breakout — price beyond the first-15m range = directional intent
  3. VWAP position      — institutional participation proxy; trade with it
  4. Relative volume    — abnormal participation validates the move

Plus filters: ATR% in a tradable band, and an index-trend gate so we don't
fight a falling market. Long-only in v1.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

DECISION_TIME = "10:00"
OPEN_RANGE_MIN = 15
ATR_WINDOW = 14
MIN_ATR_PCT = 0.008
MAX_ATR_PCT = 0.06


@dataclass
class Score:
    ticker: str
    score: float
    components: dict
    price: float
    vwap: float
    orh: float  # opening range high
    stop: float
    target: float


def _vwap(bars: pd.DataFrame) -> pd.Series:
    tp = (bars["High"] + bars["Low"] + bars["Close"]) / 3
    vol = bars["Volume"].clip(lower=1)
    return (tp * vol).cumsum() / vol.cumsum()


def _atr_pct(daily: pd.DataFrame) -> float:
    if len(daily) < ATR_WINDOW + 1:
        return np.nan
    h, l, c = daily["High"], daily["Low"], daily["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(ATR_WINDOW).mean().iloc[-1]
    return float(atr / c.iloc[-1])


def _clip01(x: float, lo: float, hi: float) -> float:
    return float(np.clip((x - lo) / (hi - lo), 0.0, 1.0))


def score_stock(intraday: pd.DataFrame, daily: pd.DataFrame, date: pd.Timestamp,
                decision_time: str = DECISION_TIME, or_minutes: int = OPEN_RANGE_MIN) -> Score | None:
    day = intraday[intraday.index.date == date.date()]
    upto = day[day.index <= pd.Timestamp(f"{date.date()} {decision_time}", tz=day.index.tz)]
    if len(upto) < 2 or daily.empty:
        return None

    prev_close = float(daily[daily.index.date < date.date()]["Close"].iloc[-1]) \
        if (daily.index.date < date.date()).any() else float(day["Open"].iloc[0])
    open_ = float(day["Open"].iloc[0])
    close = float(upto["Close"].iloc[-1])
    atr_pct = _atr_pct(daily)
    if not np.isfinite(atr_pct) or not (MIN_ATR_PCT <= atr_pct <= MAX_ATR_PCT):
        return None

    gap_pct = (open_ - prev_close) / prev_close
    orh = float(day[day.index <= day.index[0] + pd.Timedelta(minutes=or_minutes)]["High"].max())
    vwap = float(_vwap(upto).iloc[-1])
    roc = (close - open_) / open_
    avg_vol = float(daily["Volume"].tail(20).mean())
    rel_vol = float(upto["Volume"].sum() / max(avg_vol, 1))  # share of a full day's volume

    c_gap = _clip01(gap_pct, 0.003, 0.03) - _clip01(gap_pct, 0.05, 0.10) * 0.5
    c_orb = 1.0 if close > orh else 0.4 * _clip01(close / orh, 0.99, 1.0)
    c_vwap = 1.0 if close > vwap else 0.3 * _clip01(close / vwap, 0.995, 1.0)
    c_roc = _clip01(roc, 0.0, 0.02)
    c_vol = _clip01(rel_vol, 0.05, 0.30)

    score = 0.25 * c_gap + 0.25 * c_orb + 0.20 * c_vwap + 0.15 * c_roc + 0.15 * c_vol

    risk = max(close - min(orh, vwap), close * atr_pct * 0.5)
    stop = close - risk
    target = close + 1.5 * risk

    return Score(
        ticker="", score=round(score, 4),
        components={"gap_pct": round(gap_pct * 100, 2), "orb": c_orb, "vwap_above": bool(close > vwap),
                    "roc_pct": round(roc * 100, 2), "rel_vol": round(rel_vol, 2),
                    "atr_pct": round(atr_pct * 100, 2)},
        price=round(close, 2), vwap=round(vwap, 2), orh=round(orh, 2),
        stop=round(stop, 2), target=round(target, 2),
    )


def index_bullish(index_bars: pd.DataFrame, date: pd.Timestamp, decision_time: str = DECISION_TIME) -> bool:
    day = index_bars[index_bars.index.date == date.date()]
    upto = day[day.index <= pd.Timestamp(f"{date.date()} {decision_time}", tz=day.index.tz)]
    if len(upto) < 2:
        return True  # no data → don't gate
    return float(upto["Close"].iloc[-1]) > float(_vwap(upto).iloc[-1])


def rank(universe: dict, date: pd.Timestamp, index_bars: pd.DataFrame | None = None,
         decision_time: str = DECISION_TIME, require_bullish_index: bool = True,
         or_minutes: int = OPEN_RANGE_MIN) -> list[Score]:
    # Hard gate: backtest showed longs only had positive expectancy on days the
    # index traded above its own VWAP at decision time. No pick = valid output.
    if index_bars is not None and require_bullish_index \
            and not index_bullish(index_bars, date, decision_time):
        return []
    out = []
    for t, bars in universe.items():
        s = score_stock(bars.intraday, bars.daily, date, decision_time, or_minutes)
        if s is None:
            continue
        s.ticker = t
        out.append(s)
    return sorted(out, key=lambda s: s.score, reverse=True)
