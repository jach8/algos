"""Deterministic round-trip cost per share (reproducible — no random slippage)."""
from __future__ import annotations

from ..execution import COMMISSION_PER_SHARE, DEFAULT_SPY_SPREAD, SLIPPAGE_MAX_BPS

MEAN_SLIPPAGE_FRAC = (SLIPPAGE_MAX_BPS / 2.0) / 10_000.0  # mean of U[0,3] bps


def round_trip_cost_per_share(price: float) -> float:
    """Commission (both legs) + one full spread + slippage (both legs), in $/share."""
    commission = 2.0 * COMMISSION_PER_SHARE
    spread = DEFAULT_SPY_SPREAD
    slippage = 2.0 * MEAN_SLIPPAGE_FRAC * price
    return commission + spread + slippage
