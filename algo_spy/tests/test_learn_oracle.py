from algo_spy.learn.oracle import buy_and_hold_per_share, oracle_pnl_per_share


def test_oracle_captures_v_shape_cost_free():
    closes = [100.0, 98.0, 96.0, 99.0, 102.0]  # down then up
    # short 100->96 (+4) then long 96->102 (+6) = +10
    assert oracle_pnl_per_share(closes, cost_per_share=0.0) == 10.0


def test_oracle_is_upper_bound_on_buy_and_hold():
    closes = [100.0, 98.0, 96.0, 99.0, 102.0]
    bh = buy_and_hold_per_share(closes)
    # One long entry pays cost once; oracle must beat (or match) cost-adjusted hold.
    assert oracle_pnl_per_share(closes, cost_per_share=1.0) >= bh - 1.0


def test_high_cost_suppresses_flipping():
    closes = [100.0, 98.0, 96.0, 99.0, 102.0]
    # With a large switching cost, flipping short->long is not worth it.
    cheap = oracle_pnl_per_share(closes, cost_per_share=0.0)
    pricey = oracle_pnl_per_share(closes, cost_per_share=5.0)
    assert pricey < cheap
