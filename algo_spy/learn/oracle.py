"""Per-day optimal-P&L ceiling under real action constraints (DP over bar closes).

Cost convention: the full round-trip `cost_per_share` is charged once, at the moment a
directional position is *opened* (covers both the entry and the eventual exit leg). This
keeps the DP one-dimensional in cost while matching how a real trade pays commission +
spread + slippage across its lifetime.
"""
from __future__ import annotations

FLAT, LONG, SHORT = 0, 1, 2


def oracle_pnl_per_share(closes: list[float], *, cost_per_share: float) -> float:
    """Max net P&L/share over {flat, long, short}; opening a position pays cost once."""
    if len(closes) < 2:
        return 0.0
    neg = float("-inf")
    # value[state] = best cumulative net P&L with previous bar ended in `state`.
    value = [0.0, neg, neg]
    for i in range(1, len(closes)):
        dp = closes[i] - closes[i - 1]
        prev = value
        cur = [neg, neg, neg]
        cur[FLAT] = max(prev[FLAT], prev[LONG], prev[SHORT])
        cur[LONG] = max(
            prev[LONG] + dp,
            max(prev[FLAT], prev[SHORT]) - cost_per_share + dp,
        )
        cur[SHORT] = max(
            prev[SHORT] - dp,
            max(prev[FLAT], prev[LONG]) - cost_per_share - dp,
        )
        value = cur
    return max(value)


def buy_and_hold_per_share(closes: list[float]) -> float:
    if len(closes) < 2:
        return 0.0
    return closes[-1] - closes[0]
