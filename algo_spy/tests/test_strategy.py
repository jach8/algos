from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from algo_spy.bars import BAR_5M_SEC, Bar, BarAggregator, EmaStack
from algo_spy.strategy import (
    DEFAULT_EMA_MODE,
    DEFAULT_TRAIL_ACTIVATION_PCT,
    DEFAULT_TRAIL_MIN_HOLD_SEC,
    DEFAULT_TRAIL_PCT,
    MIN_HOLD_SEC,
    REENTRY_COOLDOWN_SEC,
    STOP_PCT,
    TIME_STOP_SEC,
    Strategy,
)
from algo_spy.throughput import ENTRY_DEBOUNCE as TP_ENTRY_DEBOUNCE, EXIT_MEDIUM_CONFIRM, T_ENTRY


@pytest.fixture(autouse=True)
def _deterministic_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("algo_spy.strategy.commission_for_shares", lambda _qty: 0.0)
    monkeypatch.setattr("algo_spy.execution.slippage_fraction", lambda: 0.0)


def _tick(strat: Strategy, ts_sec: float, price: float) -> list[dict]:
    return strat.on_price(ts_sec, price)


def _bullish_rates() -> dict:
    return {
        "market_high_rate_30s": 21,
        "market_low_rate_30s": 6,
        "market_high_rate_1m": 36,
        "market_low_rate_1m": 8,
        "market_high_rate_5m": 272,
        "market_low_rate_5m": 41,
        "market_high_rate_20m": 1363,
        "market_low_rate_20m": 224,
    }


def _scenario_a_rates() -> dict:
    return {
        "market_high_rate_30s": 4,
        "market_low_rate_30s": 2,
        "market_high_rate_1m": 15,
        "market_low_rate_1m": 21,
        "market_high_rate_5m": 96,
        "market_low_rate_5m": 87,
        "market_high_rate_20m": 726,
        "market_low_rate_20m": 273,
    }


def _warmup_v2(strat: Strategy, n: int = 12, base: float = 100.0, step: float = 2.0) -> float:
    """Rising 5m history; sync EMA filter tracker with seeded closes."""
    bars = [
        Bar(
            bucket_epoch=i,
            open=base + i * step,
            high=base + i * step,
            low=base + i * step,
            close=base + i * step,
        )
        for i in range(n)
    ]
    for bar in bars:
        strat.ema.update(bar.close)
        if strat.ema.ready():
            e1, e2, e3 = strat.ema.levels()
            assert e1 is not None and e2 is not None and e3 is not None
            strat.ema_filter.on_bar_close(
                close=bar.close, ema1=e1, ema2=e2, ema3=e3
            )
    strat.prev_5m_bar = bars[-1]
    px = base + n * step
    strat.bars_5m.on_tick(n * BAR_5M_SEC + 10.0, px)
    strat.last_price = px
    return px


def _debounce_bullish_tape(strat: Strategy, ts: float) -> None:
    for i in range(TP_ENTRY_DEBOUNCE):
        strat.on_tape_market(_bullish_rates(), ts + i)


def _close_next_5m_bar(strat: Strategy, bucket: int, close: float) -> list[dict]:
    ts_end = bucket * BAR_5M_SEC + BAR_5M_SEC + 0.5
    return _tick(strat, ts_end, close)


def test_bar_aggregator_rolls_5m():
    agg = BarAggregator(period_sec=BAR_5M_SEC)
    agg.on_tick(0.5, 100.0)
    agg.on_tick(200.0, 101.0)
    agg.on_tick(299.0, 100.5)
    agg.on_tick(300.5, 102.0)
    closed = agg.pop_closed()
    assert len(closed) == 1
    b = closed[0]
    assert b.open == 100.0 and b.close == 100.5


def test_ema_stack_ready_after_slow_period():
    s = EmaStack()
    for px in range(100, 109):
        s.update(float(px))
        assert not s.ready()
    s.update(109.0)
    assert s.ready()


def test_v2_long_entry_on_5m_bar_close_with_breadth():
    strat = Strategy()
    assert strat.ema_mode == DEFAULT_EMA_MODE
    px = _warmup_v2(strat, n=12)
    ema1, _, ema3 = strat.ema.levels()
    assert ema1 is not None and ema3 is not None
    t0 = 12 * BAR_5M_SEC
    _debounce_bullish_tape(strat, t0)
    emits = _close_next_5m_bar(strat, bucket=12, close=px + 2.0)
    fills = [e for e in emits if e["type"] == "ALGO_FILL" and e["side"] == "BUY"]
    assert fills, f"expected long entry, debug={strat.debug_status()}"
    assert strat.account.position is not None
    assert strat.account.position.qty > 0


def test_breadth_mode_blocks_long_when_subscores_mixed():
    """Short bearish but medium bullish → no long (precursor without confirmation)."""
    strat = Strategy(ema_mode="breadth")
    px = _warmup_v2(strat, n=12)
    mixed = {
        "market_high_rate_30s": 4,
        "market_low_rate_30s": 20,
        "market_high_rate_1m": 15,
        "market_low_rate_1m": 25,
        "market_high_rate_5m": 96,
        "market_low_rate_5m": 87,
        "market_high_rate_20m": 726,
        "market_low_rate_20m": 273,
    }
    t0 = 12 * BAR_5M_SEC
    for i in range(TP_ENTRY_DEBOUNCE):
        strat.on_tape_market(mixed, t0 + i)
    snap = strat.throughput.breadth_snapshot()
    assert snap.score > T_ENTRY
    emits = _close_next_5m_bar(strat, bucket=12, close=px + 2.0)
    assert all(e["type"] != "ALGO_FILL" for e in emits)


def test_breadth_mode_no_ema_structural_exit():
    strat = Strategy(ema_mode="breadth")
    px = _warmup_v2(strat, n=12)
    _debounce_bullish_tape(strat, 12 * BAR_5M_SEC)
    _close_next_5m_bar(strat, bucket=12, close=px + 2.0)
    assert strat.account.position is not None
    entry = strat.account.position.entry_price
    _, ema2, _ = strat.ema.levels()
    assert ema2 is not None
    # Dip below EMA2 but stay above stop — full mode would exit ema_structural here.
    dip = max(ema2 - 0.05, entry * (1.0 - STOP_PCT + 0.005))
    emits = _close_next_5m_bar(strat, bucket=13, close=dip)
    fill_reasons = [
        e.get("reason", "")
        for e in emits
        if e["type"] == "ALGO_FILL" and e.get("side") == "SELL"
    ]
    assert "ema_structural" not in fill_reasons
    if strat.account.closed:
        assert strat.account.closed[-1].reason != "ema_structural"


def test_scenario_a_no_short_at_bar_close():
    """5/28-style tape: short TFs bearish but aggregate score still bullish → no short."""
    strat = Strategy()
    px = _warmup_v2(strat, n=12)
    t0 = 12 * BAR_5M_SEC
    for i in range(TP_ENTRY_DEBOUNCE):
        strat.on_tape_market(_scenario_a_rates(), t0 + i)
    snap = strat.throughput.breadth_snapshot()
    assert snap.score > T_ENTRY
    assert not strat.throughput.allows_short_entry(snap, spy_structure_up=True)
    emits = _close_next_5m_bar(strat, bucket=12, close=px)
    short_fills = [
        e
        for e in emits
        if e["type"] == "ALGO_FILL"
        and e.get("reason", "").startswith("breadth_short")
    ]
    assert not short_fills


def test_no_entry_without_breadth_debounce():
    strat = Strategy()
    px = _warmup_v2(strat, n=12)
    strat.on_tape_market(_bullish_rates(), 12 * BAR_5M_SEC)
    emits = _close_next_5m_bar(strat, bucket=12, close=px + 1.0)
    assert all(e["type"] != "ALGO_FILL" for e in emits)


def test_stop_loss_triggers_on_tick():
    strat = Strategy()
    px = _warmup_v2(strat, n=12)
    _debounce_bullish_tape(strat, 12 * BAR_5M_SEC)
    _close_next_5m_bar(strat, bucket=12, close=px + 2.0)
    entry = strat.account.position.entry_price
    out = _tick(strat, 12 * BAR_5M_SEC + 400, entry * (1.0 - STOP_PCT - 0.001))
    assert any(e["type"] == "ALGO_FILL" and e["side"] == "SELL" for e in out)
    assert strat.account.closed[-1].reason == "stop_loss"


def test_min_hold_is_five_minutes():
    assert MIN_HOLD_SEC == 300.0
    assert REENTRY_COOLDOWN_SEC == 300.0


def test_breadth_exit_blocked_in_divergence():
    strat = Strategy()
    px = _warmup_v2(strat, n=12)
    _debounce_bullish_tape(strat, 12 * BAR_5M_SEC)
    _close_next_5m_bar(strat, bucket=12, close=px + 2.0)
    entry_ts = strat.entry_ts_sec
    assert entry_ts is not None
    strat.last_spy_new_high_ts = entry_ts + 10
    bearish_short = {
        "market_high_rate_30s": 2,
        "market_low_rate_30s": 20,
        "market_high_rate_1m": 5,
        "market_low_rate_1m": 25,
        "market_high_rate_5m": 272,
        "market_low_rate_5m": 41,
        "market_high_rate_20m": 1363,
        "market_low_rate_20m": 224,
    }
    t = entry_ts + MIN_HOLD_SEC + 1
    out: list[dict] = []
    for i in range(EXIT_MEDIUM_CONFIRM):
        out.extend(strat.on_tape_market(bearish_short, t + i))
    assert strat.account.position is not None


def test_reentry_blocked_during_cooldown():
    strat = Strategy()
    px = _warmup_v2(strat, n=12)
    _debounce_bullish_tape(strat, 12 * BAR_5M_SEC)
    _close_next_5m_bar(strat, bucket=12, close=px + 2.0)
    entry_ts = strat.entry_ts_sec
    assert entry_ts is not None
    out = _tick(strat, entry_ts + 1, px * (1.0 - STOP_PCT - 0.01))
    assert strat.account.position is None
    assert strat.last_exit_ts_sec is not None
    t = strat.last_exit_ts_sec + REENTRY_COOLDOWN_SEC - 1
    _debounce_bullish_tape(strat, t)
    emits = _close_next_5m_bar(strat, bucket=13, close=px + 3.0)
    assert all(e["type"] != "ALGO_FILL" for e in emits)


def test_trail_defaults_match_commission_breakeven():
    strat = Strategy()
    assert strat.trail_activation_pct == DEFAULT_TRAIL_ACTIVATION_PCT
    assert strat.trail_pct == DEFAULT_TRAIL_PCT
    assert strat.trail_min_hold_sec == DEFAULT_TRAIL_MIN_HOLD_SEC
    assert strat.trail_min_hold_sec < MIN_HOLD_SEC
    assert strat.entry_cutoff_et == (15, 30)
    # 756.98 long → arm ~758.49 (+0.20%)
    entry = 756.98
    assert entry * (1.0 + strat.trail_activation_pct) == pytest.approx(758.49, abs=0.02)


def test_past_entry_cutoff():
    strat = Strategy(entry_cutoff_et=(15, 30))
    tz = ZoneInfo("America/New_York")
    assert strat._past_entry_cutoff(datetime(2026, 6, 1, 15, 35, 0, tzinfo=tz).timestamp())
    assert strat._past_entry_cutoff(datetime(2026, 6, 1, 15, 30, 0, tzinfo=tz).timestamp())
    assert not strat._past_entry_cutoff(datetime(2026, 6, 1, 15, 29, 59, tzinfo=tz).timestamp())
    assert not strat._past_entry_cutoff(datetime(2026, 6, 1, 16, 5, 0, tzinfo=tz).timestamp())
    assert not strat._past_entry_cutoff(datetime(2026, 6, 1, 8, 0, 0, tzinfo=tz).timestamp())


def test_entry_blocked_when_past_cutoff(monkeypatch: pytest.MonkeyPatch):
    strat = Strategy()
    px = _warmup_v2(strat, n=12)
    monkeypatch.setattr(strat, "_past_entry_cutoff", lambda _ts: True)
    _debounce_bullish_tape(strat, 12 * BAR_5M_SEC)
    emits = _close_next_5m_bar(strat, bucket=12, close=px + 2.0)
    assert all(e.get("type") != "ALGO_FILL" for e in emits)


def test_time_stop_skipped_when_breadth_still_aligned():
    strat = Strategy()
    px = _warmup_v2(strat, n=12)
    _debounce_bullish_tape(strat, 12 * BAR_5M_SEC)
    _close_next_5m_bar(strat, bucket=12, close=px + 2.0)
    entry_ts = strat.entry_ts_sec
    entry = strat.account.position.entry_price
    assert entry_ts is not None
    t = entry_ts + TIME_STOP_SEC + 1
    _debounce_bullish_tape(strat, t - 1)
    _tick(strat, t, entry * (1.0 + 0.001))
    assert strat.account.position is not None


def test_time_stop_fires_when_breadth_not_aligned():
    strat = Strategy()
    px = _warmup_v2(strat, n=12)
    _debounce_bullish_tape(strat, 12 * BAR_5M_SEC)
    _close_next_5m_bar(strat, bucket=12, close=px + 2.0)
    entry_ts = strat.entry_ts_sec
    entry = strat.account.position.entry_price
    assert entry_ts is not None
    t = entry_ts + TIME_STOP_SEC + 1
    bearish_medium = {
        "market_high_rate_30s": 2,
        "market_low_rate_30s": 20,
        "market_high_rate_1m": 5,
        "market_low_rate_1m": 25,
        "market_high_rate_5m": 41,
        "market_low_rate_5m": 272,
        "market_high_rate_20m": 224,
        "market_low_rate_20m": 1363,
    }
    strat.on_tape_market(bearish_medium, t)
    out = _tick(strat, t, entry * (1.0 + 0.001))
    assert any(e["type"] == "ALGO_FILL" and e["side"] == "SELL" for e in out)
    assert strat.account.closed[-1].reason == "time_stop"


def test_trailing_stop_inactive_until_profit_threshold():
    strat = Strategy()
    px = _warmup_v2(strat, n=12)
    _debounce_bullish_tape(strat, 12 * BAR_5M_SEC)
    _close_next_5m_bar(strat, bucket=12, close=px + 2.0)
    entry = strat.account.position.entry_price
    entry_ts = strat.entry_ts_sec
    assert entry_ts is not None
    t = entry_ts + strat.trail_min_hold_sec + 1
    arm = strat.trail_activation_pct
    trail = strat.trail_pct
    # Small favorable move — below activation threshold
    small_up = entry * (1.0 + arm * 0.5)
    _tick(strat, t, small_up)
    pullback = small_up * (1.0 - trail * 2)
    _tick(strat, t + 1, pullback)
    assert strat.account.position is not None


def test_trailing_stop_long_locks_profit():
    strat = Strategy()
    px = _warmup_v2(strat, n=12)
    _debounce_bullish_tape(strat, 12 * BAR_5M_SEC)
    _close_next_5m_bar(strat, bucket=12, close=px + 2.0)
    entry = strat.account.position.entry_price
    entry_ts = strat.entry_ts_sec
    assert entry_ts is not None
    t = entry_ts + strat.trail_min_hold_sec + 1
    arm = strat.trail_activation_pct
    trail = strat.trail_pct
    peak = entry * (1.0 + arm + 0.0001)
    _tick(strat, t, peak)
    exit_px = peak * (1.0 - trail - 0.0001)
    out = _tick(strat, t + 1, exit_px)
    assert any(e["type"] == "ALGO_FILL" and e["side"] == "SELL" for e in out)
    assert strat.account.closed[-1].reason == "trailing_stop"


def test_trailing_stop_before_five_minute_min_hold():
    strat = Strategy()
    px = _warmup_v2(strat, n=12)
    _debounce_bullish_tape(strat, 12 * BAR_5M_SEC)
    _close_next_5m_bar(strat, bucket=12, close=px + 2.0)
    entry = strat.account.position.entry_price
    entry_ts = strat.entry_ts_sec
    assert entry_ts is not None
    t = entry_ts + strat.trail_min_hold_sec + 1
    assert t < entry_ts + MIN_HOLD_SEC
    arm = strat.trail_activation_pct
    trail = strat.trail_pct
    peak = entry * (1.0 + arm + 0.0001)
    _tick(strat, t, peak)
    exit_px = peak * (1.0 - trail - 0.0001)
    out = _tick(strat, t + 1, exit_px)
    assert any(e["type"] == "ALGO_FILL" and e["side"] == "SELL" for e in out)
    assert strat.account.closed[-1].reason == "trailing_stop"
