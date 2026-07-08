from algo_spy.learn.oracle import oracle_pnl_per_share
from algo_spy.learn.oracle_trace import oracle_entry_events, oracle_path_states


def test_oracle_path_v_shape_cost_free():
    closes = [100.0, 98.0, 96.0, 99.0, 102.0]
    _, pnl = oracle_path_states(closes, cost_per_share=0.0)
    assert pnl == 10.0
    entries = oracle_entry_events(closes, cost_per_share=0.0)
    assert len(entries) == 2
    assert entries[0] == (1, -1)
    assert entries[1] == (3, 1)
