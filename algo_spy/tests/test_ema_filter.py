from __future__ import annotations

from algo_spy.ema_filter import EmaFilterTracker, T_EMA_SOFT


def test_long_allows_when_above_ema3_and_ema1_above_ema2():
    tracker = EmaFilterTracker()
    for close in (100.0, 102.0, 104.0):
        snap = tracker.on_bar_close(close=close, ema1=105.0, ema2=103.0, ema3=101.0)
    assert snap.allows_long()
    assert snap.long_score >= T_EMA_SOFT
    assert not snap.hard_veto_long


def test_one_bar_ema1_below_ema2_is_reload_not_veto():
    tracker = EmaFilterTracker()
    tracker.on_bar_close(close=104.0, ema1=105.0, ema2=103.0, ema3=101.0)
    snap = tracker.on_bar_close(close=103.5, ema1=102.5, ema2=103.0, ema3=101.0)
    assert snap.allows_long_reload()
    assert not snap.bearish_ignition


def test_two_bar_ema1_below_ema2_is_bearish_ignition():
    tracker = EmaFilterTracker()
    tracker.on_bar_close(close=104.0, ema1=105.0, ema2=103.0, ema3=101.0)
    tracker.on_bar_close(close=103.0, ema1=102.0, ema2=103.0, ema3=101.0)
    snap = tracker.on_bar_close(close=102.0, ema1=101.0, ema2=103.0, ema3=101.0)
    assert snap.bearish_ignition
    assert snap.long_score <= 0


def test_compression_caution_when_spread_23_narrows_and_ema3_rises():
    tracker = EmaFilterTracker()
    tracker.on_bar_close(close=110.0, ema1=112.0, ema2=108.0, ema3=100.0)
    snap = tracker.on_bar_close(close=111.0, ema1=111.5, ema2=108.5, ema3=101.0)
    assert snap.compression_caution
    assert not snap.bearish_ignition
