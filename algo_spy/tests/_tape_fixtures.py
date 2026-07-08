"""Synthetic tape builders for learn/ tests."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def write_synthetic_uptrend_tape(tmp_path: Path) -> Path:
    """SPY rising 0.1/tick with bullish breadth; 1s ticks over ~100 min.

    Long enough for EmaStack (10 closed 5m bars ≈ 50 min) to become ready and still
    leave room for the forward label horizon.
    """
    start = datetime(2026, 6, 26, 13, 30, 0, tzinfo=timezone.utc)
    path = tmp_path / "20260626_tape.jsonl"
    with path.open("w") as f:
        price = 700.0
        for i in range(6000):
            ts = (start + timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            price += 0.1
            row = {
                "ts": ts,
                "symbol": "SPY",
                "event": "price_update",
                "market_high_rate_30s": 50,
                "market_low_rate_30s": 2,
                "market_high_rate_1m": 50,
                "market_low_rate_1m": 2,
                "market_high_rate_5m": 60,
                "market_low_rate_5m": 3,
                "market_high_rate_20m": 80,
                "market_low_rate_20m": 5,
                "last_price": round(price, 2),
            }
            f.write(json.dumps(row) + "\n")
    return path
