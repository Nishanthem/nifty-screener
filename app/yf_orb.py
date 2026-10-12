"""Delayed fallback for the 5-min ORB scan when NSE blocks the live path.

Pulls today's 5-min bars from yfinance (~15 min delayed) and reports every
Nifty 50 stock whose 5-min candle closing at 09:35/09:40/09:45 closed above
(long) or below (short) its 09:15-09:30 opening range by >= 0.1%, on the
matching side of VWAP (long above, short below).

`python -m app.yf_orb --cmp 10` instead lists stocks whose latest price is
beyond the first-10-min range (09:15-09:25), same buffer and VWAP rules.
"""

from __future__ import annotations

import sys

import pandas as pd
import yfinance as yf

from .constituents import NIFTY50, INDEX_TICKER, yf_symbol
from .nse_live import SCAN_CLOSES, Breakout
from .signals import _vwap, breakout_side

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
            v = float(_vwap(df[df.index <= start]).iloc[-1])
            side = breakout_side(c, orh, orl, v)
            if side is not None:
                rows.append((t, round(orh, 2), round(orl, 2), Breakout(side, hhmm, round(c, 2), round(v, 2))))
                break
    return info, sorted(rows, key=lambda x: (x[3].close_at, x[0]))


def cmp_scan(or_minutes: int) -> tuple[list, int]:
    """Stocks whose latest 5-min bar close (CMP proxy; the bar still forming
    after the range) is beyond the first-`or_minutes` range. Returns
    (rows, n_not_ready): stocks whose feed has no bar after the range yet are
    counted as not ready instead of being silently dropped."""
    today = pd.Timestamp.now(tz=IST).normalize()
    or_end = pd.Timestamp(f"{today.date()} 09:15", tz=IST) + pd.Timedelta(minutes=or_minutes)
    rows, not_ready = [], 0
    for t in NIFTY50:
        df = _bars(yf_symbol(t))
        if df.empty:
            not_ready += 1
            continue
        o = df[df.index < or_end]
        if len(o) < or_minutes // 5 or df.index[-1] < or_end:
            not_ready += 1
            continue
        orh, orl = float(o.High.max()), float(o.Low.min())
        c = float(df.Close.iloc[-1])
        v = float(_vwap(df).iloc[-1])
        side = breakout_side(c, orh, orl, v)
        if side is not None:
            rows.append((t, round(orh, 2), round(orl, 2),
                         Breakout(side, df.index[-1].strftime("%H:%M"), round(c, 2), round(v, 2))))
    return sorted(rows, key=lambda x: (x[3].side, x[0])), not_ready


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--cmp":
        m = int(sys.argv[2])
        rows, not_ready = cmp_scan(m)
        now = pd.Timestamp.now(tz=IST)
        print(f"[DELAYED yfinance] CMP vs {m}m opening range ({now:%Y-%m-%d %H:%M} IST): {len(rows)}")
        if not_ready:
            print(f"  ({not_ready} stocks skipped: yfinance has no bar after the range yet — feed lagging)")
        for sym, orh, orl, b in rows:
            arrow = "ABOVE" if b.side == "long" else "BELOW"
            print(f"  {sym:<12} {arrow} OR  cmp={b.close} @ {b.close_at} bar  vwap={b.vwap}  orh={orh}  orl={orl}")
        return
    closes = tuple(sys.argv[1:]) or SCAN_CLOSES
    info, rows = scan(closes)
    now = pd.Timestamp.now(tz=IST)
    print(f"[DELAYED yfinance] 5m ORB vs 15m OR, closes {closes} ({now:%Y-%m-%d %H:%M} IST): {len(rows)}")
    if info["index"]:
        orh, orl, last, at = info["index"]
        print(f"  NIFTY 50: OR {orl}-{orh}, last {last} @ {at}")
    for sym, orh, orl, b in rows:
        arrow = "ABOVE" if b.side == "long" else "BELOW"
        print(f"  {sym:<12} {arrow} OR  close={b.close} @ {b.close_at}  vwap={b.vwap}  orh={orh}  orl={orl}")


if __name__ == "__main__":
    main()
