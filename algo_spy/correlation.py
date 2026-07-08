"""Rolling Pearson correlation: SPY returns vs changes in market throughput net."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field


@dataclass
class RollingCorrelation:
    """Pairwise rolling correlation of (throughput_net delta, SPY simple return)."""

    window: int = 120
    min_samples: int = 20
    _tp_deltas: deque[float] = field(default_factory=deque)
    _spy_returns: deque[float] = field(default_factory=deque)
    _prev_price: float | None = None
    _prev_throughput_net: float | None = None
    last_value: float | None = None

    def observe(self, spy_price: float, throughput_net: float) -> float | None:
        """Record one SPY tick with the current aggregated throughput net (high−low sum)."""
        if (
            self._prev_price is not None
            and self._prev_price > 0
            and self._prev_throughput_net is not None
        ):
            spy_ret = (spy_price - self._prev_price) / self._prev_price
            tp_delta = throughput_net - self._prev_throughput_net
            self._tp_deltas.append(tp_delta)
            self._spy_returns.append(spy_ret)
            while len(self._tp_deltas) > self.window:
                self._tp_deltas.popleft()
                self._spy_returns.popleft()
            self.last_value = _pearson(list(self._tp_deltas), list(self._spy_returns))

        self._prev_price = spy_price
        self._prev_throughput_net = throughput_net
        return self.last_value


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = 0.0
    vx = 0.0
    vy = 0.0
    for x, y in zip(xs, ys):
        dx = x - mx
        dy = y - my
        cov += dx * dy
        vx += dx * dx
        vy += dy * dy
    denom = math.sqrt(vx * vy)
    if denom <= 0:
        return None
    return cov / denom
