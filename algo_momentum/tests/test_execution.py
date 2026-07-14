from __future__ import annotations

import pytest

from algo_momentum.execution import (
    COMMISSION_PER_SHARE,
    commission_for_shares,
    fill_price,
    quote_spread,
)


def test_quote_spread_from_nbbo():
    assert quote_spread(50.0, 50.05) == pytest.approx(0.05)


def test_fill_price_buy_at_ask_without_slippage():
    px, spread = fill_price(
        "BUY",
        last_price=50.0,
        bid=49.99,
        ask=50.02,
        slippage_fn=lambda: 0.0,
    )
    assert spread == pytest.approx(0.03)
    assert px == pytest.approx(50.02)


def test_fill_price_sell_at_bid_without_slippage():
    px, spread = fill_price(
        "SELL",
        last_price=50.0,
        bid=49.99,
        ask=50.02,
        slippage_fn=lambda: 0.0,
    )
    assert px == pytest.approx(49.99)


def test_commission_per_share_matches_breadth_algo():
    assert COMMISSION_PER_SHARE == 0.65
    assert commission_for_shares(10) == pytest.approx(6.5)


def test_missing_nbbo_uses_default_spread_half():
    px, spread = fill_price(
        "BUY",
        last_price=100.0,
        bid=None,
        ask=None,
        slippage_fn=lambda: 0.0,
    )
    assert spread == pytest.approx(0.02)
    assert px == pytest.approx(100.01)
