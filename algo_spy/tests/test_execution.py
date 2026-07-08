from __future__ import annotations

import pytest

from algo_spy.execution import (
    COMMISSION_PER_SHARE,
    commission_for_shares,
    fill_price,
    quote_spread,
)


def test_quote_spread_from_nbbo():
    assert quote_spread(750.0, 750.01) == pytest.approx(0.01)


def test_fill_price_buy_at_ask_without_slippage():
    px, spread = fill_price(
        "BUY",
        last_price=750.0,
        bid=749.99,
        ask=750.01,
        slippage_fn=lambda: 0.0,
    )
    assert spread == pytest.approx(0.02)
    assert px == pytest.approx(750.01)


def test_fill_price_sell_at_bid_without_slippage():
    px, spread = fill_price(
        "SELL",
        last_price=750.0,
        bid=749.99,
        ask=750.01,
        slippage_fn=lambda: 0.0,
    )
    assert px == pytest.approx(749.99)


def test_commission_per_share():
    assert COMMISSION_PER_SHARE == 0.65
    assert commission_for_shares(13) == pytest.approx(8.45)


def test_execution_applied_on_enter_exit():
    from algo_spy.strategy import Strategy

    strat = Strategy()
    strat.last_bid = 100.0
    strat.last_ask = 100.02
    fill_px, spread, comm = strat._simulate_fill("BUY", 100.01, 10.0)
    assert spread == pytest.approx(0.02)
    assert comm == pytest.approx(6.5)
    assert fill_px >= 100.02
