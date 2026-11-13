"""Opening reversal setups: long-tailed 5-min candle at 09:15-09:30 + confirmations.

A long lower tail after a down move = sellers absorbed (bullish reversal);
a long upper tail after an up move = buyers rejected (bearish reversal).
Each candidate is scored on confirmations computed with data known at 09:30:

    against   the tail opposes the move from prev close (it's a reversal, not a continuation)
    rsi       RSI(14) on 5m, extreme over the opening candles: <= RSI_LO bull / >= RSI_HI bear
    volume    tail candle volume vs the same 5m slot's 10-day average
    level     tail tested the previous day's low (bull) / high (bear)
    vwap      09:25 close back on the reversal side of session VWAP

Trade model (backtest): enter 09:30 open, stop just beyond the tail extreme,
target TARGET_R, square off 15:15.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

from .wicks import WICK_BODY_RATIO, WICK_RANGE_SHARE

IST = "Asia/Kolkata"
BULL, BEAR = "bull", "bear"
SLOTS = ("09:15", "09:20", "09:25")
DECISION = "09:30"
EXIT = "15:15"

MIN_WICK_PCT = 0.25       # tail must be at least this % of price
RSI_PERIOD = 14
RSI_LO, RSI_HI = 35, 65
REL_VOL_MIN = 1.5
PC_TOL = 0.0002            # within 0.02% of prev close counts as touching it
LEVEL_TOL = 0.002          # within 0.2% of prev day low/high counts as a test
MIN_AGAINST_PCT = 0.3      # move into the tail of at least 0.3% from prev close
TARGET_R = 1.5
STOP_BUFFER = 0.0005


def rsi(close: pd.Series, n: int = RSI_PERIOD) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


@dataclass
class Setup:
    ticker: str
    date: str
    time: str             # tail candle start
    side: str             # bull | bear
    wick_pct: float
    move_pct: float       # prev close -> tail extreme, signed
    rsi: float
    rel_vol: float
    level_pct: float      # tail extreme vs prev day low (bull) / high (bear), signed toward break
    vwap_ok: bool
    entry: float
    stop: float
    prev_close: float = np.nan
    pc_touch: bool = False  # tail candle's range touched the previous day's close
    flags: dict = field(default_factory=dict)

    @property
    def confirmations(self) -> int:
        return sum(self.flags.values())


def _flags(s: Setup) -> dict:
    bull = s.side == BULL
    return {
        "against": (s.move_pct <= -MIN_AGAINST_PCT) if bull else (s.move_pct >= MIN_AGAINST_PCT),
        "rsi": bool(np.isfinite(s.rsi) and ((s.rsi <= RSI_LO) if bull else (s.rsi >= RSI_HI))),
        "volume": bool(np.isfinite(s.rel_vol) and s.rel_vol >= REL_VOL_MIN),
        "level": bool(np.isfinite(s.level_pct) and s.level_pct >= -LEVEL_TOL * 100),
        "vwap": s.vwap_ok,
    }


def _tail(o: float, h: float, l: float, c: float) -> tuple[str, float] | None:
    rng = h - l
    if rng <= 0:
        return None
    body = abs(c - o)
    for side, w in ((BULL, min(o, c) - l), (BEAR, h - max(o, c))):
        if w >= WICK_BODY_RATIO * body and w / rng >= WICK_RANGE_SHARE and w / c * 100 >= MIN_WICK_PCT:
            return side, w / c * 100
    return None


def day_setups(ticker: str, bars: pd.DataFrame, date, rsi_series: pd.Series,
               slot_vol: pd.Series, prev: dict, decision: str = DECISION,
               start: str = "09:15") -> list[Setup]:
    """Setups for one stock-day from tail candles in [start, decision).
    `bars` = that day's 5m bars; `prev` = prev day close/high/low."""
    day = pd.Timestamp(date).strftime("%Y-%m-%d")
    t = bars.index.strftime("%H:%M")
    opening = bars[(t >= "09:15") & (t < decision)]
    window = opening[opening.index.strftime("%H:%M") >= start]
    if window.empty:
        return []
    tp = (opening["High"] + opening["Low"] + opening["Close"]) / 3
    vol = opening["Volume"].replace(0, np.nan)
    vwap = float((tp * vol).sum() / vol.sum()) if vol.notna().any() else float(tp.mean())
    after = bars[t >= decision]
    entry = float(after["Open"].iloc[0]) if not after.empty else float(opening["Close"].iloc[-1])
    last_close = float(opening["Close"].iloc[-1])

    out = []
    for ts, b in window.iterrows():
        hit = _tail(*(float(b[k]) for k in ("Open", "High", "Low", "Close")))
        if hit is None:
            continue
        side, wick_pct = hit
        bull = side == BULL
        upto = opening.loc[:ts]
        extreme = float(upto["Low"].min() if bull else upto["High"].max())
        r = rsi_series.reindex(window.loc[:ts].index)
        r_ext = float(r.min() if bull else r.max())
        slot = ts.strftime("%H:%M")
        avg = slot_vol.get(slot, np.nan)
        rv = float(b["Volume"]) / avg if avg and np.isfinite(avg) and b["Volume"] > 0 else np.nan
        lvl = ((prev["low"] - extreme) / prev["low"] if bull else (extreme - prev["high"]) / prev["high"]) * 100
        stop = extreme * (1 - STOP_BUFFER) if bull else extreme * (1 + STOP_BUFFER)
        s = Setup(ticker=ticker, date=day, time=slot, side=side, wick_pct=round(wick_pct, 2),
                  move_pct=round((extreme - prev["close"]) / prev["close"] * 100, 2),
                  rsi=round(r_ext, 1) if np.isfinite(r_ext) else np.nan,
                  rel_vol=round(rv, 2) if np.isfinite(rv) else np.nan, level_pct=round(lvl, 2),
                  vwap_ok=(last_close > vwap) if bull else (last_close < vwap),
                  entry=round(entry, 2), stop=round(stop, 2), prev_close=round(prev["close"], 2),
                  pc_touch=touches(b, prev["close"]))
        s.flags = _flags(s)
        out.append(s)
    return out


def touches(bar, level: float, tol: float = PC_TOL) -> bool:
    return float(bar["Low"]) <= level * (1 + tol) and float(bar["High"]) >= level * (1 - tol)


def first_touch(bars: pd.DataFrame, date, level: float, decision: str = DECISION,
                start: str = "09:15") -> str | None:
    """Start time (HH:MM) of the first candle in [start, decision) whose range touched `level`."""
    t = bars.index.strftime("%H:%M")
    for ts, b in bars[(t >= start) & (t < decision)].iterrows():
        if touches(b, level):
            return ts.strftime("%H:%M")
    return None


def best_per_day(setups: list[Setup]) -> list[Setup]:
    """One setup per stock-day: most confirmations, then biggest tail."""
    best: dict[tuple, Setup] = {}
    for s in setups:
        k = (s.ticker, s.date)
        if k not in best or (s.confirmations, s.wick_pct) > (best[k].confirmations, best[k].wick_pct):
            best[k] = s
    return list(best.values())


def _prev_levels(intraday: pd.DataFrame) -> pd.DataFrame:
    g = intraday.groupby(intraday.index.date)
    lv = pd.DataFrame({"close": g["Close"].last(), "high": g["High"].max(), "low": g["Low"].min()})
    return lv.shift(1)


def _slot_volume(intraday: pd.DataFrame, lookback: int = 10) -> pd.DataFrame:
    """date x slot -> mean volume of that slot over the previous `lookback` days."""
    v = intraday["Volume"].replace(0, np.nan)
    piv = v.groupby([intraday.index.date, intraday.index.strftime("%H:%M")]).sum(min_count=1).unstack()
    return piv.rolling(lookback, min_periods=3).mean().shift(1)


def scan_frame(ticker: str, intraday: pd.DataFrame, dates=None, decision: str = DECISION,
               official_close: pd.Series | None = None, start: str = "09:15") -> list[Setup]:
    """All setups for a stock over `dates` (default: every day in the frame).

    `official_close` (date -> exchange close) replaces the last 5m bar's close as
    the previous close; the official close is the 15:30 auction price.
    """
    if intraday.empty:
        return []
    r = rsi(intraday["Close"])
    prev = _prev_levels(intraday)
    if official_close is not None and not official_close.empty:
        oc = official_close.copy()
        oc.index = pd.Index([pd.Timestamp(i).date() for i in oc.index])
        prev["close"] = [oc[oc.index < d].iloc[-1] if (oc.index < d).any() else c
                         for d, c in zip(prev.index, prev["close"])]
    sv = _slot_volume(intraday)
    out = []
    for d, bars in intraday.groupby(intraday.index.date):
        if dates is not None and d not in dates:
            continue
        p = prev.loc[d]
        if p.isna().any():
            continue
        out += day_setups(ticker, bars, d, r, sv.loc[d] if d in sv.index else pd.Series(dtype=float),
                          p.to_dict(), decision, start)
    return out


def outcome(s: Setup, bars: pd.DataFrame, target_r: float = TARGET_R, decision: str = DECISION) -> dict:
    """R-multiple of the trade plus forward % moves in the reversal direction."""
    t = bars.index.strftime("%H:%M")
    path = bars[(t >= decision) & (t < EXIT)]
    bull = s.side == BULL
    risk = (s.entry - s.stop) if bull else (s.stop - s.entry)
    if path.empty or risk <= 0:
        return {"r": np.nan}
    target = s.entry + target_r * risk if bull else s.entry - target_r * risk
    r_mult = None
    for _, b in path.iterrows():
        if (b["Low"] <= s.stop) if bull else (b["High"] >= s.stop):
            r_mult = -1.0
            break
        if (b["High"] >= target) if bull else (b["Low"] <= target):
            r_mult = target_r
            break
    exit_px = float(path["Close"].iloc[-1])
    sign = 1 if bull else -1
    if r_mult is None:
        r_mult = sign * (exit_px - s.entry) / risk
    at = path[path.index.strftime("%H:%M") <= "10:30"]
    return {
        "r": round(r_mult, 3),
        "ret_1030": round(sign * (float(at["Close"].iloc[-1]) - s.entry) / s.entry * 100, 3),
        "ret_eod": round(sign * (exit_px - s.entry) / s.entry * 100, 3),
        "risk_pct": round(risk / s.entry * 100, 3),
    }


def to_frame(setups: list[Setup]) -> pd.DataFrame:
    rows = []
    for s in setups:
        d = asdict(s)
        d.update({f"f_{k}": v for k, v in d.pop("flags").items()})
        d["confirmations"] = s.confirmations
        rows.append(d)
    return pd.DataFrame(rows)
