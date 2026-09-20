"""Minimal web UI: FastAPI serving the daily suggestion."""

from __future__ import annotations

import pandas as pd
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .data import YFinanceProvider, load_all
from .signals import rank, LONG
from .wicks import long_wicks, describe, day_candles
from . import backtest

app = FastAPI(title="Nifty Intraday Screener")
_provider = YFinanceProvider()

PAGE = """<!doctype html><html><head><title>Nifty Intraday Screener</title>
<style>body{font-family:system-ui;max-width:900px;margin:2rem auto;padding:0 1rem}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:6px 8px;text-align:left}
th{background:#f4f4f4}.pick{background:#e8f7e8}.meta{color:#666;font-size:.9em}</style>
</head><body>{{body}}</body></html>"""


def _table(picks, wicks: dict[str, str]) -> str:
    rows = "".join(
        f"<tr class='{'pick' if i == 0 else ''}'><td>{'BUY' if s.side == LONG else 'SELL'}</td>"
        f"<td>{s.ticker}</td><td>{s.score:.2f}</td>"
        f"<td>{s.price}</td><td>{s.stop}</td><td>{s.target}</td>"
        f"<td>{s.components['gap_pct']}%</td><td>{s.components['rel_vol']}</td>"
        f"<td>{'yes' if s.components['vwap_above'] else 'no'}</td><td>{wicks.get(s.ticker, '')}</td></tr>"
        for i, s in enumerate(picks)
    )
    return ("<table><tr><th>Side</th><th>Stock</th><th>Score</th><th>Price</th><th>Stop</th>"
            "<th>Target</th><th>Gap</th><th>RelVol</th><th>&gt;VWAP</th><th>Wicks (first 3 x 15m)</th></tr>"
            f"{rows}</table>")


@app.get("/", response_class=HTMLResponse)
def today(top: int = 5, time: str = "10:00", side: str = "auto"):
    index_bars = _provider.index_intraday()
    universe = load_all(_provider)
    date = pd.Timestamp.now(tz="Asia/Kolkata").normalize()
    picks = rank(universe, date, index_bars, decision_time=time, side=side)[:top]
    wicks = {s.ticker: describe(long_wicks(day_candles(universe[s.ticker].intraday, date))) for s in picks}
    body = (f"<h1>Nifty 50 Intraday — {date.date()}</h1>"
            f"<p class='meta'>Decision time {time} IST · delayed data · side={side} · "
            f"not investment advice</p>")
    body += _table(picks, wicks) if picks else "<p>No candidates passed filters (index gate).</p>"
    return PAGE.replace("{{body}}", body)


@app.get("/backtest")
def bt(side: str = LONG):
    index_bars = _provider.index_intraday()
    universe = load_all(_provider)
    return backtest.summarize(backtest.run(universe, index_bars, side=side))
