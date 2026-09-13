"""Walk-forward sanity check of the scoring logic.

For each trading day in the data, take the top-ranked stock at DECISION_TIME,
enter at that bar's close, and exit at first of (stop, target) touched on
subsequent bars, else square off at 15:15 IST. Reports hit rate, expectancy
in R multiples, and compares against holding the index for the same window.

yfinance 15m history ~60 days — enough for a smoke test, not a statistic.
"""

from __future__ import annotations

import pandas as pd

from .signals import rank, DECISION_TIME

EXIT_TIME = "15:15"


def simulate_day(universe: dict, index_bars: pd.DataFrame, date: pd.Timestamp,
                 decision_time: str = DECISION_TIME, or_minutes: int | None = None,
                 top_n: int = 1) -> dict | None:
    kwargs = {} if or_minutes is None else {"or_minutes": or_minutes}
    picks = rank(universe, date, index_bars, decision_time=decision_time, **kwargs)
    if not picks:
        return None
    top = picks[0]
    day = universe[top.ticker].intraday
    day = day[day.index.date == date.date()]
    after = day[day.index > pd.Timestamp(f"{date.date()} {decision_time}", tz=day.index.tz)]
    after = after[after.index <= pd.Timestamp(f"{date.date()} {EXIT_TIME}", tz=day.index.tz)]
    if after.empty:
        return None

    entry = top.price
    risk = entry - top.stop
    exit_px = float(after["Close"].iloc[-1])
    hit = "eod"
    for _, bar in after.iterrows():
        if bar["Low"] <= top.stop:
            exit_px, hit = top.stop, "stop"
            break
        if bar["High"] >= top.target:
            exit_px, hit = top.target, "target"
            break
    r = (exit_px - entry) / max(risk, 1e-9)

    # index baseline: buy index at decision, exit 15:15
    idx_day = index_bars[index_bars.index.date == date.date()]
    idx_ret = 0.0
    if not idx_day.empty:
        idx_in = idx_day[idx_day.index <= pd.Timestamp(f"{date.date()} {decision_time}", tz=idx_day.index.tz)]
        idx_out = idx_day[idx_day.index <= pd.Timestamp(f"{date.date()} {EXIT_TIME}", tz=idx_day.index.tz)]
        if len(idx_in) and len(idx_out):
            idx_ret = (float(idx_out["Close"].iloc[-1]) - float(idx_in["Close"].iloc[-1])) / float(idx_in["Close"].iloc[-1])

    return {"date": str(date.date()), "pick": top.ticker, "score": top.score,
            "entry": entry, "exit": round(exit_px, 2), "R": round(r, 2), "exit_type": hit,
            "index_pct": round(idx_ret * 100, 2)}


def run(universe: dict, index_bars: pd.DataFrame,
        decision_time: str = DECISION_TIME, or_minutes: int | None = None) -> pd.DataFrame:
    dates = sorted({d.date() for d in index_bars.index})
    rows = [r for d in dates
            if (r := simulate_day(universe, index_bars, pd.Timestamp(d, tz=index_bars.index.tz),
                                  decision_time, or_minutes))]
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"error": "no trades simulated"}
    wins = (df["R"] > 0).mean()
    return {
        "trades": int(len(df)),
        "hit_rate": round(float(wins), 3),
        "expectancy_R": round(float(df["R"].mean()), 3),
        "avg_index_pct_same_window": round(float(df["index_pct"].mean()), 3),
        "stopouts": int((df["exit_type"] == "stop").sum()),
        "targets": int((df["exit_type"] == "target").sum()),
    }
