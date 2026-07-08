from __future__ import annotations

import math

from algo_spy.correlation import RollingCorrelation, _pearson


def test_pearson_perfect_positive():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [2.0, 4.0, 6.0, 8.0, 10.0]
    r = _pearson(xs, ys)
    assert r is not None
    assert math.isclose(r, 1.0, abs_tol=1e-9)


def test_pearson_perfect_negative():
    xs = [1.0, 2.0, 3.0, 4.0]
    ys = [-1.0, -2.0, -3.0, -4.0]
    r = _pearson(xs, ys)
    assert r is not None
    assert math.isclose(r, -1.0, abs_tol=1e-9)


def test_rolling_correlation_warms_up():
    rc = RollingCorrelation(window=10, min_samples=3)
    assert rc.observe(100.0, 10.0) is None
    assert rc.observe(101.0, 12.0) is None
    r = rc.observe(102.0, 15.0)
    assert r is not None
    assert -1.0 <= r <= 1.0
