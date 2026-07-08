from algo_spy.learn.dataset import build_candidates_for_tape
from algo_spy.tests._tape_fixtures import write_synthetic_uptrend_tape


def test_uptrend_long_candidates_win(tmp_path):
    path = write_synthetic_uptrend_tape(tmp_path)
    cands = build_candidates_for_tape(path, label_horizon_sec=600)
    longs = [c for c in cands if c.side > 0 and c.label is not None]
    assert longs, "expected at least one labeled long candidate in an uptrend"
    assert sum(c.label for c in longs) / len(longs) > 0.5
