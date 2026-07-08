"""OHLC aggregation from ticks + EMA stack (default periods for 5m trend)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional

BAR_1M_SEC = 60
BAR_5M_SEC = 300


@dataclass
class Bar:
    """bucket_epoch: floor(ts_seconds / bar_period)."""

    bucket_epoch: int
    open: float
    high: float
    low: float
    close: float
    ticks: int = 0


@dataclass
class BarAggregator:
    """Roll ticks into OHLC bars of `period_sec` (60 = 1m, 300 = 5m)."""

    period_sec: int = BAR_1M_SEC
    current: Optional[Bar] = None
    closed: Deque[Bar] = field(default_factory=deque)

    def on_tick(self, ts_sec: float, price: float) -> None:
        bucket = int(ts_sec // self.period_sec)
        if self.current is None:
            self.current = Bar(bucket, price, price, price, price, 1)
            return
        if bucket == self.current.bucket_epoch:
            b = self.current
            if price > b.high:
                b.high = price
            if price < b.low:
                b.low = price
            b.close = price
            b.ticks += 1
            return
        prev_close = self.current.close
        self.closed.append(self.current)
        gap = bucket - self.current.bucket_epoch - 1
        for i in range(1, gap + 1):
            b = self.current.bucket_epoch + i
            self.closed.append(Bar(b, prev_close, prev_close, prev_close, prev_close, 0))
        self.current = Bar(bucket, price, price, price, price, 1)

    def pop_closed(self) -> list[Bar]:
        out = list(self.closed)
        self.closed.clear()
        return out


class Ema:
    """Standard EMA: alpha = 2/(period+1). Seeds with first value."""

    def __init__(self, period: int):
        if period < 1:
            raise ValueError("period must be >= 1")
        self.period = period
        self.alpha = 2.0 / (period + 1.0)
        self.value: Optional[float] = None

    def update(self, x: float) -> float:
        if self.value is None:
            self.value = x
        else:
            self.value = self.alpha * x + (1.0 - self.alpha) * self.value
        return self.value


@dataclass
class EmaStack:
    """EMA1/EMA2/EMA3 on 5m closes (periods 3 / 6 / 10)."""

    fast: Ema = field(default_factory=lambda: Ema(3))
    mid: Ema = field(default_factory=lambda: Ema(6))
    slow: Ema = field(default_factory=lambda: Ema(10))
    bars_seen: int = 0

    def update(self, close: float) -> None:
        self.fast.update(close)
        self.mid.update(close)
        self.slow.update(close)
        self.bars_seen += 1

    def ready(self) -> bool:
        return self.bars_seen >= self.slow.period

    def levels(self) -> tuple[Optional[float], Optional[float], Optional[float]]:
        return self.fast.value, self.mid.value, self.slow.value

    def is_uptrend(self) -> bool:
        e1, e2, e3 = self.levels()
        if not self.ready() or e1 is None or e2 is None or e3 is None:
            return False
        return e1 > e2 > e3

    def is_downtrend(self) -> bool:
        e1, e2, e3 = self.levels()
        if not self.ready() or e1 is None or e2 is None or e3 is None:
            return False
        return e1 < e2 < e3
