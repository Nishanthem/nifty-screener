"""Delayed fallback for the 5-min ORB scan when NSE blocks the live path.

Pulls today's 5-min bars from yfinance (~15 min delayed) and reports every
Nifty 50 stock whose 5-min candle closing at 09:35/09:40/09:45 closed above
(long) or below (short) its 09:15-09:30 opening range.
"""

from __future__ import annotations

import sys

import pandas as pd
import yfinance as yf

from .constituents import NIFTY50, INDEX_TICKER, yf_symbol
from .nse_live import SCAN_CLOSES, Breakout

IST = "Asia/Kolkata"


def _bars(symbol: str) -> pd.DataFrame:
    df = yf.download(symbol, period="1d", interval="5m", progress=False,
                     auto_adjust=False, multi_level_index=False)
    if df.empty:
        return df
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(IST)
    return df


def scan(closes: tuple[str, ...] = SCAN_CLOSES) -> tuple[dict, list]:
    today = pd.Timestamp.now(tz=IST).normalize()
    idx = _bars(INDEX_TICKER)
    info = {"index": None}
    if not idx.empty:
        o = idx.between_time("09:15", "09:29")
        if not o.empty:
            info["index"] = (round(float(o.High.max()), 2), round(float(o.Low.min()), 2),
                             round(float(idx.Close.iloc[-1]), 2), idx.index[-1].strftime("%H:%M"))
    rows = []
    for t in NIFTY50:
        df = _bars(yf_symbol(t))
        if df.empty:
            continue
        o = df.between_time("09:15", "09:29")
        if len(o) < 3:
            continue
        orh, orl = float(o.High.max()), float(o.Low.min())
        for hhmm in closes:
            end = pd.Timestamp(f"{today.date()} {hhmm}", tz=IST)
            start = end - pd.Timedelta(minutes=5)
            if start not in df.index:
                continue
            c = float(df.loc[start, "Close"])
            if c > orh:
                rows.append((t, round(orh, 2), round(orl, 2), Breakout("long", hhmm, round(c, 2))))
                break
            if c < orl:
                rows.append((t, round(orh, 2), round(orl, 2), Breakout("short", hhmm, round(c, 2))))
                break
    return info, sorted(rows, key=lambda x: (x[3].close_at, x[0]))


def main() -> None:
    closes = tuple(sys.argv[1:]) or SCAN_CLOSES
    info, rows = scan(closes)
    now = pd.Timestamp.now(tz=IST)
    print(f"[DELAYED yfinance] 5m ORB vs 15m OR, closes {closes} ({now:%Y-%m-%d %H:%M} IST): {len(rows)}")
    if info["index"]:
        orh, orl, last, at = info["index"]
        print(f"  NIFTY 50: OR {orl}-{orh}, last {last} @ {at}")
    for sym, orh, orl, b in rows:
        arrow = "ABOVE" if b.side == "long" else "BELOW"
        print(f"  {sym:<12} {arrow} OR  close={b.close} @ {b.close_at}  orh={orh}  orl={orl}")


if __name__ == "__main__":
    main()
