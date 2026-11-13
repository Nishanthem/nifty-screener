"""Rolling 5-min tail scan: check every candle as it closes, all session.

Later in the day a long tail in the middle of the range is mostly noise, so
each tail is tagged with the context that makes it a reversal:

    day_extreme   the tail printed a new session low (bull) / high (bear)
    rsi           RSI(14, 5m) at the tail <= RSI_LO (bull) / >= RSI_HI (bear)
    level         tail tested the previous day's low (bull) / high (bear)
    prev_close    tail candle's range touched the previous day's close

Trade model (backtest): enter next candle's open, stop just beyond the tail
candle's extreme, 1.5R target, square off 15:15.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .reversal import (_tail, rsi, touches, _prev_levels, BULL, RSI_LO, RSI_HI, LEVEL_TOL,
                       STOP_BUFFER, TARGET_R, EXIT)

IST = "Asia/Kolkata"
LAST_CANDLE = "14:55"   # no new entries in the last 20 minutes


@dataclass
class Tail:
    ticker: str
    date: str
    time: str           # candle start HH:MM
    side: str
    wick_pct: float
    rsi: float
    close: float
    stop: float
    prev_close: float
    flags: dict = field(default_factory=dict)

    @property
    def tags(self) -> list[str]:
        return [k for k, v in self.flags.items() if v]

    @property
    def strong(self) -> bool:
        """The two combinations that held up in the backtest: RSI extreme at a
        prev-day high/low, or a new day extreme right at the previous close."""
        f = self.flags
        return (f["rsi"] and f["level"]) or (f["day_extreme"] and f["prev_close"])


def day_tails(ticker: str, bars: pd.DataFrame, rsi_series: pd.Series, prev: dict,
              until: str = LAST_CANDLE) -> list[Tail]:
    """Every long-tail candle in one day's 5m bars, using only data up to each candle's close."""
    out = []
    lo_so_far, hi_so_far = np.inf, -np.inf
    for ts, b in bars.iterrows():
        o, h, l, c = (float(b[k]) for k in ("Open", "High", "Low", "Close"))
        new_low, new_high = l < lo_so_far, h > hi_so_far
        lo_so_far, hi_so_far = min(lo_so_far, l), max(hi_so_far, h)
        t = ts.strftime("%H:%M")
        if t > until:
            break
        hit = _tail(o, h, l, c)
        if hit is None:
            continue
        side, wick_pct = hit
        bull = side == BULL
        r = float(rsi_series.get(ts, np.nan))
        lvl = (prev["low"] - l) / prev["low"] if bull else (h - prev["high"]) / prev["high"]
        tail = Tail(ticker=ticker, date=ts.strftime("%Y-%m-%d"), time=t, side=side,
                    wick_pct=round(wick_pct, 2), rsi=round(r, 1) if np.isfinite(r) else np.nan,
                    close=round(c, 2),
                    stop=round(l * (1 - STOP_BUFFER) if bull else h * (1 + STOP_BUFFER), 2),
                    prev_close=round(prev["close"], 2))
        tail.flags = {
            "day_extreme": bool(new_low if bull else new_high),
            "rsi": bool(np.isfinite(r) and ((r <= RSI_LO) if bull else (r >= RSI_HI))),
            "level": bool(lvl >= -LEVEL_TOL),
            "prev_close": touches(b, prev["close"]),
        }
        out.append(tail)
    return out


def scan_frame(ticker: str, intraday: pd.DataFrame, dates=None,
               official_close: pd.Series | None = None, until: str = LAST_CANDLE) -> list[Tail]:
    if intraday.empty:
        return []
    r = rsi(intraday["Close"])
    prev = _prev_levels(intraday)
    if official_close is not None and not official_close.empty:
        oc = official_close.copy()
        oc.index = pd.Index([pd.Timestamp(i).date() for i in oc.index])
        prev["close"] = [oc[oc.index < d].iloc[-1] if (oc.index < d).any() else c
                         for d, c in zip(prev.index, prev["close"])]
    out = []
    for d, bars in intraday.groupby(intraday.index.date):
        if dates is not None and d not in dates:
            continue
        p = prev.loc[d]
        if p.isna().any():
            continue
        out += day_tails(ticker, bars, r, p.to_dict(), until)
    return out


def outcome(tail: Tail, bars: pd.DataFrame, target_r: float = TARGET_R) -> dict:
    """Enter at the next candle's open; same stop/target/square-off model as the 09:30 scan."""
    after = bars[bars.index > pd.Timestamp(f"{tail.date} {tail.time}", tz=IST)]
    after = after[after.index.strftime("%H:%M") < EXIT]
    if after.empty:
        return {"r": np.nan}
    bull = tail.side == BULL
    entry = float(after["Open"].iloc[0])
    risk = (entry - tail.stop) if bull else (tail.stop - entry)
    if risk <= 0:
        return {"r": np.nan}
    target = entry + target_r * risk if bull else entry - target_r * risk
    for _, b in after.iterrows():
        if (b["Low"] <= tail.stop) if bull else (b["High"] >= tail.stop):
            return {"r": -1.0, "risk_pct": round(risk / entry * 100, 3)}
        if (b["High"] >= target) if bull else (b["Low"] <= target):
            return {"r": target_r, "risk_pct": round(risk / entry * 100, 3)}
    sign = 1 if bull else -1
    return {"r": round(sign * (float(after["Close"].iloc[-1]) - entry) / risk, 3),
            "risk_pct": round(risk / entry * 100, 3)}


def _bulk_5m(tickers, days: int = 5) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    from .constituents import yf_symbol
    df = yf.download([yf_symbol(t) for t in tickers], period=f"{days}d", interval="5m",
                     progress=False, auto_adjust=True, group_by="ticker")
    out = {}
    for t in tickers:
        try:
            d = df[yf_symbol(t)].dropna(how="all")
        except KeyError:
            continue
        if d.index.tz is None:
            d.index = d.index.tz_localize("UTC")
        d.index = d.index.tz_convert(IST)
        out[t] = d
    return out


def scan_now(date: pd.Timestamp, live: bool = False, tickers=None) -> list[Tail]:
    """All of today's long-tail candles completed so far."""
    from .constituents import NIFTY50
    from .wick_scan import _official_closes
    tickers = tickers or NIFTY50
    frames = _bulk_5m(tickers)
    closes = _official_closes(tickers, date)
    if live:
        from .nse_live import candles_from_snapshots, load_snapshots
        snaps = load_snapshots(date)
        pc = (snaps[snaps["prev_close"] > 0].groupby("symbol")["prev_close"].first().to_dict()
              if not snaps.empty else {})
        for t in tickers:
            today = candles_from_snapshots(date, t, minutes=5)
            if today.empty or t not in frames:
                continue
            today = today.assign(Volume=np.nan)
            today.index = pd.DatetimeIndex(today.index).tz_convert(IST)
            hist = frames[t]
            frames[t] = pd.concat([hist[hist.index.date < date.date()], today])
            if t in pc:
                closes[t] = pd.Series([pc[t]], index=[(date - pd.Timedelta(days=1)).date()])
    else:
        # yfinance includes the still-forming candle; drop it
        now = pd.Timestamp.now(tz=IST)
        frames = {t: f[f.index + pd.Timedelta(minutes=5) <= now] for t, f in frames.items()}
    out = []
    for t, f in frames.items():
        out += scan_frame(t, f, {date.date()}, official_close=closes.get(t))
    return out


def fmt(t: Tail) -> str:
    tags = ",".join(t.tags) or "-"
    return (f"{'STRONG' if t.strong else '      '} {t.time} {'BUY ' if t.side == BULL else 'SELL'} "
            f"{t.ticker:<11} tail {t.wick_pct:.2f}%  RSI {t.rsi:>5}  close {t.close}  stop {t.stop}  [{tags}]")


def watch(live: bool = False, strong_only: bool = False, poll_s: int = 60) -> None:
    """Print each new long-tail candle within ~1 minute of it closing, until 15:00 IST.

    live=True polls NSE snapshots every `poll_s` (Chrome/CDP needed) and builds
    candles from them; otherwise uses yfinance 5m bars, which lag the exchange.
    """
    seen: set[tuple] = set()
    last_scan = None
    while True:
        now = pd.Timestamp.now(tz=IST)
        hhmm = now.strftime("%H:%M")
        if hhmm >= "15:00":
            print("15:00 IST — watch finished.")
            return
        if hhmm < "09:15":
            _time.sleep(30)
            continue
        if live:
            from .nse_live import fetch_snapshots, persist
            try:
                persist(fetch_snapshots())
            except Exception as e:  # keep watching through a transient NSE/CDP failure
                print(f"poll failed: {e}")
        bucket = now.floor("5min")
        if bucket != last_scan and now - bucket >= pd.Timedelta(seconds=20):
            last_scan = bucket
            for t in sorted(scan_now(now.normalize(), live), key=lambda x: (x.time, x.ticker)):
                key = (t.ticker, t.time, t.side)
                if key in seen or (strong_only and not t.strong):
                    continue
                seen.add(key)
                print(fmt(t), flush=True)
        _time.sleep(poll_s if live else 20)


def backtest(tickers=None) -> pd.DataFrame:
    from .constituents import NIFTY50
    from .data import YFinanceProvider
    from .wick_scan import _official_closes
    tickers = tickers or NIFTY50
    p = YFinanceProvider()
    closes = _official_closes(tickers, pd.Timestamp.now(tz=IST) - pd.Timedelta(days=90))
    rows = []
    for t in tickers:
        df = p.intraday(t, days=60, interval="5m")
        days = {d: b for d, b in df.groupby(df.index.date)}
        for tl in scan_frame(t, df, official_close=closes.get(t)):
            o = outcome(tl, days[pd.Timestamp(tl.date).date()])
            rows.append({"ticker": t, "date": tl.date, "time": tl.time, "side": tl.side,
                         "strong": tl.strong, **{f"f_{k}": v for k, v in tl.flags.items()}, **o})
    return pd.DataFrame(rows).dropna(subset=["r"])


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    def row(d):
        return pd.Series({"tails": len(d), "hit_%": round((d.r > 0).mean() * 100, 1),
                          "avg_R": round(d.r.mean(), 3)})
    early, late = df.time < "09:30", df.time >= "09:30"
    groups = {"all": df, "strong": df[df.strong],
              "09:15-09:30 all": df[early], "09:15-09:30 strong": df[early & df.strong],
              "after 09:30 all": df[late], "after 09:30 strong": df[late & df.strong]}
    for f in ("day_extreme", "rsi", "level", "prev_close"):
        groups[f"+{f}"] = df[df[f"f_{f}"]]
    return pd.DataFrame({k: row(v) for k, v in groups.items() if len(v)}).T
