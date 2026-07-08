from __future__ import annotations

from core.reporting import risk_reward_ratio, trade_expectancy


def test_trade_expectancy_mixed():
    # 60% win rate, avg win +10, avg loss -5
    e = trade_expectancy(0.6, 10.0, -5.0)
    assert abs(e - 4.0) < 1e-9


def test_risk_reward_ratio_and_breakeven():
    result = risk_reward_ratio(9.0, -6.0)
    assert result is not None
    ratio, breakeven = result
    assert ratio == "2:3"
    assert abs(breakeven - 40.0) < 1e-9
