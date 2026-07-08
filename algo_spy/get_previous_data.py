"""Fetch recent 5m bars from Yahoo Finance to warm up the EMA stack before live tape."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .bars import BAR_5M_SEC, Bar

if TYPE_CHECKING:
    from .strategy import Strategy

DEFAULT_WARMUP_BARS = 120  # ~2 hours of 5m bars; EMA3 needs 10 closes


def fetch_5m_bars(symbol: str, max_bars: int = DEFAULT_WARMUP_BARS) -> list[Bar]:
    """Return closed 5m OHLC bars, oldest first. Skips the still-forming bucket."""
    import yfinance as yf

    hist = yf.Ticker(symbol).history(period="5d", interval="1m", auto_adjust=False)
    if hist is None or hist.empty:
        return []

    now_bucket = int(time.time() // BAR_5M_SEC)
    bars: list[Bar] = []
    for ts, row in hist.iterrows():
        if hasattr(ts, "timestamp"):
            epoch_sec = ts.timestamp()
        else:
            epoch_sec = float(ts) / 1e9 if float(ts) > 1e12 else float(ts)
        bucket = int(epoch_sec // BAR_5M_SEC)
        if bucket >= now_bucket:
            continue
        bars.append(Bar(
            bucket,
            float(row["Open"]),
            float(row["High"]),
            float(row["Low"]),
            float(row["Close"]),
            1,
        ))

    if max_bars > 0 and len(bars) > max_bars:
        bars = bars[-max_bars:]
    return bars


def warmup_strategy_from_yahoo(
    strat: Strategy,
    symbol: str | None = None,
    max_bars: int = DEFAULT_WARMUP_BARS,
) -> int:
    """Seed 5m EMAs from Yahoo history. Returns bar count applied."""
    sym = symbol or strat.symbol
    bars = fetch_5m_bars(sym, max_bars=max_bars)
    if not bars:
        raise RuntimeError(f"no Yahoo 5m bars returned for {sym}")
    return strat.seed_ema_from_closed_bars(bars)
