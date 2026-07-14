from __future__ import annotations

from datetime import datetime, timezone

import pytest

from algo_momentum.execution import COMMISSION_PER_SHARE
from algo_momentum.main import handle_tape_event
from algo_momentum.report import flatten_open_positions
from algo_momentum.strategy import (
    BREADTH_ENTER_BUY_PCT,
    PERSIST_MIN_CUM,
    PERSIST_MIN_RECENT,
    Strategy,
)


def _ts(sec: float = 1_700_000_000.0) -> str:
    return datetime.fromtimestamp(sec, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _seed_leader(strat: Strategy, symbol: str, *, now_sec: float, highs: int = 5) -> None:
    """Force persistence state as if the name already printed enough highs."""
    st = strat.symbols[symbol]
    st.cum_high = PERSIST_MIN_CUM + 2
    now = datetime.fromtimestamp(now_sec, tz=timezone.utc)
    for i in range(max(highs, PERSIST_MIN_RECENT)):
        st.recent.append((now, "high"))


def _market_ok(ev: dict | None = None) -> dict:
    base = {
        "type": "TAPE_EVENT",
        "event": "market_summary",
        "ts": _ts(),
        "buy_pct_5m": BREADTH_ENTER_BUY_PCT + 5,
        "market_high_rate_5m": 40,
        "market_low_rate_5m": 10,
    }
    if ev:
        base.update(ev)
    return base


def test_enter_exit_applies_commission_and_spread():
    strat = Strategy(cash_fraction=1.0, max_positions=3)
    strat.account.cash = 10_000.0
    strat.account.starting_cash = 10_000.0
    handle_tape_event(strat, _market_ok())

    ts0 = 1_700_000_100.0
    _seed_leader(strat, "NVDA", now_sec=ts0)
    strat.last_bids["NVDA"] = 100.0
    strat.last_asks["NVDA"] = 100.02

    # Monkeypatch fill slippage to zero for deterministic maths
    from algo_momentum import strategy as strat_mod

    original = strat_mod.fill_price

    def _no_slip(side, *, last_price, bid, ask, slippage_fn=None):
        return original(side, last_price=last_price, bid=bid, ask=ask, slippage_fn=lambda: 0.0)

    strat_mod.fill_price = _no_slip
    try:
        out = handle_tape_event(
            strat,
            {
                "type": "TAPE_EVENT",
                "event": "new_high",
                "symbol": "NVDA",
                "ts": _ts(ts0),
                "last_price": 100.01,
                "high_count": PERSIST_MIN_CUM + 3,
                "volume_spike": True,
                "bid": 100.0,
                "ask": 100.02,
                "buy_pct_5m": 70,
                "market_high_rate_5m": 40,
                "market_low_rate_5m": 10,
            },
        )
        assert any(e["type"] == "ALGO_FILL" and e["side"] == "BUY" for e in out)
        pos = strat.account.positions["NVDA"]
        assert pos.entry_price == pytest.approx(100.02)
        assert pos.entry_commission == pytest.approx(pos.qty * COMMISSION_PER_SHARE)

        cash_after_entry = strat.account.cash
        assert cash_after_entry < 10_000.0

        # Exit via stop — drop 5%
        stop_px = pos.entry_price * 0.95
        strat.last_bids["NVDA"] = stop_px - 0.01
        strat.last_asks["NVDA"] = stop_px + 0.01
        out2 = handle_tape_event(
            strat,
            {
                "type": "TAPE_EVENT",
                "event": "price_update",
                "symbol": "NVDA",
                "ts": _ts(ts0 + 5),
                "last_price": stop_px,
                "bid": stop_px - 0.01,
                "ask": stop_px + 0.01,
            },
        )
        assert any(e["type"] == "ALGO_FILL" and e["side"] == "SELL" for e in out2)
        assert "NVDA" not in strat.account.positions
        trade = strat.account.closed[-1]
        assert trade.reason == "stop_loss"
        expected_pnl = (trade.exit_price - trade.entry_price) * trade.qty - trade.commission
        assert trade.pnl == pytest.approx(expected_pnl)
        assert trade.commission > 0
        assert strat.account.total_commission == pytest.approx(trade.commission)
    finally:
        strat_mod.fill_price = original


def test_breadth_kill_flattens_with_costs():
    strat = Strategy(cash_fraction=1.0)
    strat.account.cash = 5_000.0
    handle_tape_event(strat, _market_ok())
    ts0 = 1_700_000_200.0
    _seed_leader(strat, "TSLA", now_sec=ts0)

    from algo_momentum import strategy as strat_mod

    original = strat_mod.fill_price

    def _no_slip(side, *, last_price, bid, ask, slippage_fn=None):
        return original(side, last_price=last_price, bid=bid, ask=ask, slippage_fn=lambda: 0.0)

    strat_mod.fill_price = _no_slip
    try:
        handle_tape_event(
            strat,
            {
                "type": "TAPE_EVENT",
                "event": "new_high",
                "symbol": "TSLA",
                "ts": _ts(ts0),
                "last_price": 200.0,
                "high_count": PERSIST_MIN_CUM + 1,
                "volume_spike": True,
                "bid": 199.98,
                "ask": 200.02,
                "buy_pct_5m": 70,
                "market_high_rate_5m": 30,
                "market_low_rate_5m": 10,
            },
        )
        assert "TSLA" in strat.account.positions
        out = handle_tape_event(
            strat,
            {
                "type": "TAPE_EVENT",
                "event": "market_summary",
                "ts": _ts(ts0 + 10),
                "buy_pct_5m": 40.0,
                "market_high_rate_5m": 5,
                "market_low_rate_5m": 20,
            },
        )
        assert "TSLA" not in strat.account.positions
        assert any(e.get("reason") == "breadth_kill_switch" for e in out if e["type"] == "ALGO_SIGNAL")
        trade = strat.account.closed[-1]
        assert trade.commission > 0
        assert trade.pnl == pytest.approx(
            (trade.exit_price - trade.entry_price) * trade.qty - trade.commission
        )
    finally:
        strat_mod.fill_price = original


def test_session_flatten_uses_fill_model():
    strat = Strategy()
    strat.account.cash = 10_000.0
    # Manually plant a position
    from algo_momentum.strategy import Position

    strat.account.positions["AAPL"] = Position(
        symbol="AAPL",
        qty=10,
        entry_price=150.0,
        entry_ts=_ts(),
        entry_ts_sec=1_700_000_000.0,
        entry_commission=6.5,
        trail_extreme=150.0,
    )
    strat.last_prices["AAPL"] = 151.0
    strat.last_bids["AAPL"] = 150.98
    strat.last_asks["AAPL"] = 151.02
    cash_before = strat.account.cash
    flatten_open_positions(strat)
    assert not strat.account.positions
    trade = strat.account.closed[-1]
    assert trade.reason == "session_end_flatten"
    assert trade.commission >= 6.5  # entry + exit
    assert strat.account.cash != cash_before


def test_pnl_metrics_multi_position():
    strat = Strategy()
    from algo_momentum.strategy import Position

    strat.account.positions["AAA"] = Position(
        symbol="AAA",
        qty=10,
        entry_price=10.0,
        entry_ts=_ts(),
        entry_ts_sec=1.0,
        entry_commission=6.5,
    )
    strat.account.positions["BBB"] = Position(
        symbol="BBB",
        qty=5,
        entry_price=20.0,
        entry_ts=_ts(),
        entry_ts_sec=1.0,
        entry_commission=3.25,
    )
    strat.last_prices["AAA"] = 11.0
    strat.last_prices["BBB"] = 19.0
    realized, unrealized, open_n = strat.pnl_metrics()
    assert open_n == 2
    assert realized == 0.0
    assert unrealized == pytest.approx(10 * 1.0 + 5 * (-1.0))


def test_no_entry_without_volume_spike():
    strat = Strategy(cash_fraction=1.0)
    handle_tape_event(strat, _market_ok())
    ts0 = 1_700_000_300.0
    _seed_leader(strat, "META", now_sec=ts0)
    out = handle_tape_event(
        strat,
        {
            "type": "TAPE_EVENT",
            "event": "new_high",
            "symbol": "META",
            "ts": _ts(ts0),
            "last_price": 500.0,
            "high_count": PERSIST_MIN_CUM + 1,
            "volume_spike": False,
            "buy_pct_5m": 70,
            "market_high_rate_5m": 40,
            "market_low_rate_5m": 10,
        },
    )
    assert out == []
    assert "META" not in strat.account.positions
