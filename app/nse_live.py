"""Live NSE data provider.

NSE's JSON endpoints reject datacenter IPs directly (403 + Akamai bot
challenge), but work from a real browser session. So we drive the existing
Chrome over CDP and fetch from within the page context.

The endpoint returns *snapshots*, not bars: open/high/low/last/volume plus
totalTradedValue, from which day VWAP = value/volume exactly. To get the
opening-range high we poll and persist snapshots from 09:15 onwards, and
build our own bars from them.

Endpoint (as of Sep 2026 — NSE moved to /api/NextApi/, undocumented and
liable to change without notice):
    /api/NextApi/apiClient/marketWatchApi?functionName=getIndicesData&symbol=NIFTY%2050
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

CDP_URL = "http://localhost:29229"
SEED_PAGE = "https://www.nseindia.com/market-data/live-equity-market"
API_PATH = ("/api/NextApi/apiClient/marketWatchApi"
            "?functionName=getIndicesData&symbol=NIFTY%2050")
SNAP_DIR = Path(__file__).resolve().parent.parent / "snapshots"
IST = "Asia/Kolkata"


@dataclass
class Snapshot:
    ts: str
    symbol: str
    last: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: float
    traded_value: float

    @property
    def vwap(self) -> float:
        return self.traded_value / self.volume if self.volume else self.last


def fetch_snapshots() -> list[Snapshot]:
    """One live snapshot of all Nifty 50 constituents (plus the index)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        page = browser.contexts[0].new_page()
        try:
            page.goto(SEED_PAGE, wait_until="domcontentloaded", timeout=60000)
            payload = page.evaluate(
                "async (path) => (await fetch(path)).json()", API_PATH
            )
        finally:
            page.close()

    rows = payload.get("data", {}).get("data", [])
    now = pd.Timestamp.now(tz=IST).isoformat()
    out = []
    for r in rows:
        if r.get("lastPrice") is None or r.get("open") in (None, 0):
            continue
        out.append(Snapshot(
            ts=r.get("lastUpdateTime") or now,
            symbol=r["symbol"],
            last=float(r["lastPrice"]),
            open=float(r["open"]),
            high=float(r["dayHigh"]),
            low=float(r["dayLow"]),
            prev_close=float(r["previousClose"]),
            volume=float(r.get("totalTradedVolume") or 0),
            traded_value=float(r.get("totalTradedValue") or 0),
        ))
    return out


def persist(snaps: list[Snapshot]) -> Path:
    """Append a snapshot batch to today's JSONL log."""
    SNAP_DIR.mkdir(exist_ok=True)
    day = pd.Timestamp.now(tz=IST).date()
    path = SNAP_DIR / f"{day}.jsonl"
    with path.open("a") as f:
        for s in snaps:
            f.write(json.dumps(asdict(s)) + "\n")
    return path


def load_snapshots(date: pd.Timestamp) -> pd.DataFrame:
    path = SNAP_DIR / f"{date.date()}.jsonl"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_json(path, lines=True)
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce", format="mixed")
    if df["ts"].dt.tz is None:
        df["ts"] = df["ts"].dt.tz_localize(IST)
    return df.dropna(subset=["ts"])


def opening_range_high(date: pd.Timestamp, symbol: str, minutes: int = 15) -> float | None:
    """Highest observed price in the first `minutes` of the session."""
    df = load_snapshots(date)
    if df.empty:
        return None
    cutoff = pd.Timestamp(f"{date.date()} 09:{15 + minutes:02d}", tz=IST)
    early = df[(df["symbol"] == symbol) & (df["ts"] <= cutoff)]
    return float(early["high"].max()) if not early.empty else None
