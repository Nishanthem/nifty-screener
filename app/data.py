"""Pluggable data layer.

YFinanceProvider is the default (free, ~15 min delayed).
To go real-time, implement the same interface with Zerodha Kite / Angel One
and pass it into score_day()/backtest() — nothing else changes.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yfinance as yf

from .constituents import NIFTY50, INDEX_TICKER, yf_symbol

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"


@dataclass
class Bars:
    ticker: str
    intraday: pd.DataFrame  # columns: Open High Low Close Volume, tz-aware IST index
    daily: pd.DataFrame     # prior daily bars for context (ATR, avg volume, prev close)


class YFinanceProvider:
    def __init__(self, cache: bool = True):
        self.cache = cache
        CACHE_DIR.mkdir(exist_ok=True)

    def _cached(self, key: str, fetch) -> pd.DataFrame:
        path = CACHE_DIR / f"{key}.pkl"
        if self.cache and path.exists():
            return pickle.loads(path.read_bytes())
        df = fetch()
        if self.cache and not df.empty:
            path.write_bytes(pickle.dumps(df))
        return df

    def clear_cache(self) -> None:
        for f in CACHE_DIR.glob("*.pkl"):
            f.unlink()

    def intraday(self, ticker: str, days: int = 60, interval: str = "15m") -> pd.DataFrame:
        return self._cached(f"i_{ticker}_{days}_{interval}", lambda: self._fetch_intraday(ticker, days, interval))

    def _fetch_intraday(self, ticker: str, days: int, interval: str) -> pd.DataFrame:
        df = yf.download(
            yf_symbol(ticker), period=f"{days}d", interval=interval,
            progress=False, auto_adjust=True, multi_level_index=False,
        )
        if df.empty:
            return df
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert("Asia/Kolkata")
        return df

    def daily(self, ticker: str, days: int = 120) -> pd.DataFrame:
        return self._cached(f"d_{ticker}_{days}", lambda: yf.download(
            yf_symbol(ticker), period=f"{days}d", interval="1d",
            progress=False, auto_adjust=True, multi_level_index=False,
        ))

    def index_intraday(self, days: int = 60, interval: str = "15m") -> pd.DataFrame:
        return self._cached(f"idx_{days}_{interval}", lambda: self._fetch_index(days, interval))

    @staticmethod
    def _fetch_index(days: int, interval: str) -> pd.DataFrame:
        df = yf.download(
            INDEX_TICKER, period=f"{days}d", interval=interval,
            progress=False, auto_adjust=True, multi_level_index=False,
        )
        if not df.empty:
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            df.index = df.index.tz_convert("Asia/Kolkata")
        return df


def load_day(provider, ticker: str) -> Bars:
    return Bars(ticker=ticker, intraday=provider.intraday(ticker), daily=provider.daily(ticker))


def load_all(provider) -> dict[str, Bars]:
    return {t: load_day(provider, t) for t in NIFTY50}
