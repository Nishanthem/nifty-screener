"""Composite intraday scoring.

Idea: at a decision time (default 10:00 IST, after the opening noise settles),
score each Nifty 50 stock on four well-studied intraday edges:

  1. Gap continuation   — moderate gaps (0.3-3%) tend to continue, large gaps fade
  2. Opening range breakout — price beyond the first-15m range = directional intent
  3. VWAP position      — institutional participation proxy; trade with it
  4. Relative volume    — abnormal participation validates the move

Plus filters: ATR% in a tradable band, and an index-trend gate so we don't
fight the market: longs only when the index is above its VWAP, shorts only
when it is below. Short scoring is the exact mirror of long scoring
(gap-down continuation, opening-range-low breakdown, below VWAP, negative ROC).
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
LONG, SHORT = "long", "short"


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
    side: str = LONG
    orl: float = float("nan")  # opening range low


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


def composite(side: str, gap_pct: float, last: float, orh: float, orl: float,
              vwap: float, roc: float, rel_vol: float, atr_pct: float,
              confirmed: bool | None = None) -> tuple[float, dict, float]:
    """Shared long/short scoring. Returns (score, components, risk-per-share).

    For shorts every directional input is mirrored: a gap *down* continues,
    a break *below* the opening-range low shows intent, price *below* VWAP
    means sellers are in control, and negative ROC is momentum.

    `confirmed` overrides the breakout test when the caller has a candle-close
    confirmation (e.g. a 5-min candle closed beyond the opening range).
    """
    sgn = 1.0 if side == LONG else -1.0
    d_gap, d_roc = sgn * gap_pct, sgn * roc
    level = orh if side == LONG else orl          # breakout level
    beyond = confirmed if confirmed is not None else sgn * (last - level) > 0
    near = _clip01(sgn * (last / level - 1.0), -0.01, 0.0)
    beyond_vwap = sgn * (last - vwap) > 0
    near_vwap = _clip01(sgn * (last / vwap - 1.0), -0.005, 0.0)

    c_gap = _clip01(d_gap, 0.003, 0.03) - _clip01(d_gap, 0.05, 0.10) * 0.5
    c_orb = 1.0 if beyond else 0.4 * near
    c_vwap = 1.0 if beyond_vwap else 0.3 * near_vwap
    c_roc = _clip01(d_roc, 0.0, 0.02)
    c_vol = _clip01(rel_vol, 0.05, 0.30)
    score = 0.25 * c_gap + 0.25 * c_orb + 0.20 * c_vwap + 0.15 * c_roc + 0.15 * c_vol

    if side == LONG:
        risk = max(last - min(orh, vwap), last * atr_pct * 0.5)
    else:
        risk = max(max(orl, vwap) - last, last * atr_pct * 0.5)
    comps = {"gap_pct": round(gap_pct * 100, 2), "orb": round(c_orb, 2),
             "vwap_above": bool(last > vwap), "roc_pct": round(roc * 100, 2),
             "rel_vol": round(rel_vol, 2), "atr_pct": round(atr_pct * 100, 2)}
    return score, comps, risk


def levels(side: str, last: float, risk: float) -> tuple[float, float]:
    """(stop, target) at 1.5R on the trade's side."""
    if side == LONG:
        return round(last - risk, 2), round(last + 1.5 * risk, 2)
    return round(last + risk, 2), round(last - 1.5 * risk, 2)


def score_stock(intraday: pd.DataFrame, daily: pd.DataFrame, date: pd.Timestamp,
                decision_time: str = DECISION_TIME, or_minutes: int = OPEN_RANGE_MIN,
                side: str = LONG) -> Score | None:
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
    orng = day[day.index <= day.index[0] + pd.Timedelta(minutes=or_minutes)]
    orh, orl = float(orng["High"].max()), float(orng["Low"].min())
    vwap = float(_vwap(upto).iloc[-1])
    roc = (close - open_) / open_
    avg_vol = float(daily["Volume"].tail(20).mean())
    rel_vol = float(upto["Volume"].sum() / max(avg_vol, 1))  # share of a full day's volume

    score, comps, risk = composite(side, gap_pct, close, orh, orl, vwap, roc, rel_vol, atr_pct)
    stop, target = levels(side, close, risk)
    return Score(
        ticker="", score=round(score, 4), components=comps,
        price=round(close, 2), vwap=round(vwap, 2), orh=round(orh, 2), orl=round(orl, 2),
        stop=stop, target=target, side=side,
    )


BOTH = "both"
INDEX_GATE_PCT = 0.003   # index >= +0.3% vs prev close -> longs only; <= -0.3% -> shorts only


def index_regime(change_pct: float) -> str:
    """LONG / SHORT / BOTH from the index's change vs previous close (fraction)."""
    if change_pct >= INDEX_GATE_PCT:
        return LONG
    if change_pct <= -INDEX_GATE_PCT:
        return SHORT
    return BOTH


def sides_allowed(regime: str | None, side: str) -> list[str]:
    """Sides to trade for a requested `side` (LONG / SHORT / "auto") under `regime`."""
    if regime is None or regime == BOTH:
        return [LONG, SHORT] if side == "auto" else [side]
    return [regime] if side in ("auto", regime) else []


def index_change(index_bars: pd.DataFrame, date: pd.Timestamp,
                 decision_time: str = DECISION_TIME) -> float | None:
    """Index change vs previous session close (fraction) at decision time, None if no data."""
    day = index_bars[index_bars.index.date == date.date()]
    upto = day[day.index <= pd.Timestamp(f"{date.date()} {decision_time}", tz=day.index.tz)]
    prev = index_bars[index_bars.index.date < date.date()]
    if upto.empty or prev.empty:
        return None
    prev_close = float(prev["Close"].iloc[-1])
    return float(upto["Close"].iloc[-1]) / prev_close - 1.0


def index_side(index_bars: pd.DataFrame, date: pd.Timestamp,
               decision_time: str = DECISION_TIME) -> str | None:
    """LONG / SHORT / BOTH from the +-0.3% index gate, None if no data."""
    chg = index_change(index_bars, date, decision_time)
    return None if chg is None else index_regime(chg)


def index_bullish(index_bars: pd.DataFrame, date: pd.Timestamp, decision_time: str = DECISION_TIME) -> bool:
    return index_side(index_bars, date, decision_time) != SHORT


def rank(universe: dict, date: pd.Timestamp, index_bars: pd.DataFrame | None = None,
         decision_time: str = DECISION_TIME, require_bullish_index: bool = True,
         or_minutes: int = OPEN_RANGE_MIN, side: str = LONG) -> list[Score]:
    """Ranked picks for `side` (LONG / SHORT / "auto").

    Hard gate on the index vs its previous close at decision time:
    >= +0.3% longs only, <= -0.3% shorts only, in between both sides.
    No pick = valid output.
    """
    idx = index_side(index_bars, date, decision_time) if index_bars is not None else None
    if not require_bullish_index and side != "auto":
        idx = None
    out = []
    for sd in sides_allowed(idx, side):
        for t, bars in universe.items():
            s = score_stock(bars.intraday, bars.daily, date, decision_time, or_minutes, sd)
            if s is None:
                continue
            s.ticker = t
            out.append(s)
    return sorted(out, key=lambda s: s.score, reverse=True)
