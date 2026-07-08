"""Realistic fill simulation: NBBO spread, random slippage, per-share commission."""
from __future__ import annotations

import random

COMMISSION_PER_SHARE = 0.65
DEFAULT_SPY_SPREAD = 0.01  # $0.01 when bid/ask not on tape
SLIPPAGE_MAX_BPS = 3.0  # adverse slippage uniform in [0, 3] bps


def slippage_fraction() -> float:
    """Adverse slippage as a fraction of price (e.g. 3 bps → 0.0003)."""
    return random.uniform(0.0, SLIPPAGE_MAX_BPS) / 10_000.0


def quote_spread(bid: float | None, ask: float | None) -> float:
    if bid is not None and ask is not None and ask >= bid:
        return ask - bid
    return DEFAULT_SPY_SPREAD


def commission_for_shares(qty: float) -> float:
    return abs(qty) * COMMISSION_PER_SHARE


def fill_price(
    side: str,
    *,
    last_price: float,
    bid: float | None,
    ask: float | None,
    slippage_fn=slippage_fraction,
) -> tuple[float, float]:
    """Return (executable fill price, quoted spread at fill time).

    Buys pay ask (+ slippage); sells receive bid (− slippage). Missing NBBO uses
    last ± half the default SPY spread.
    """
    spread = quote_spread(bid, ask)
    slip = slippage_fn()
    side_upper = side.upper()
    if side_upper == "BUY":
        base = ask if ask is not None else last_price + spread / 2.0
        return base * (1.0 + slip), spread
    if side_upper == "SELL":
        base = bid if bid is not None else last_price - spread / 2.0
        return base * (1.0 - slip), spread
    raise ValueError(f"unknown fill side: {side}")
