from __future__ import annotations

from algo_spy.throughput import (
    BREADTH_EDGE,
    MarketThroughput,
    T_ENTRY,
    T_WARN,
    divergence_state,
    window_contribution,
)


def _all_rates(rates: dict[str, tuple[int, int]]) -> dict:
    out: dict = {}
    for label, (high, low) in rates.items():
        out[f"market_high_rate_{label}"] = high
        out[f"market_low_rate_{label}"] = low
    return out


def test_partial_rate_fields_do_not_zero_missing_side():
    tp = MarketThroughput()
    tp.update_from_tape_event(_all_rates({w: (100, 20) for w in ("30s", "1m", "5m", "20m")}))
    tp.update_from_tape_event({"market_high_rate_30s": 5})
    assert tp.windows["30s"].high == 100
    assert tp.windows["30s"].low == 20


def test_window_contribution_respects_edge_and_cap():
    assert window_contribution(5, 2, "30s") == 4 * 3  # bias 3 > edge 2
    assert window_contribution(100, 0, "30s") == 4 * 40  # capped
    assert window_contribution(5, 4, "30s") == 0.0  # bias 1, within edge


def test_bullish_dashboard_strongly_positive_score():
    tp = MarketThroughput()
    tp.update_from_tape_event(_all_rates({
        "30s": (21, 6),
        "1m": (36, 8),
        "5m": (272, 41),
        "20m": (1363, 224),
    }))
    snap = tp.breadth_snapshot()
    assert snap.score > T_ENTRY
    assert snap.medium_breadth > 0
    assert snap.short_breadth > 0
    assert tp.allows_long_entry(snap)
    assert not tp.allows_short_entry(snap)


def test_scenario_a_528_1440_short_blocked():
    """5/28 ~14:40 v1 short: 30s H4/L2, 1m H15/L21, 5m H96/L87, 20m H726/L273."""
    tp = MarketThroughput()
    tp.update_from_tape_event(_all_rates({
        "30s": (4, 2),
        "1m": (15, 21),
        "5m": (96, 87),
        "20m": (726, 273),
    }))
    snap = tp.breadth_snapshot()
    assert snap.score > T_ENTRY
    assert snap.medium_breadth > 0
    assert snap.market_short_bearish()  # 1m pulls short subscore negative
    assert not snap.meets_short_entry_score()
    assert not tp.allows_short_entry(snap)
    assert divergence_state(snap, spy_structure_up=True) == "divergence"
    assert not tp.allows_short_entry(snap, spy_structure_up=True)


def test_short_horizon_flicker_medium_still_bullish():
    tp = MarketThroughput()
    tp.update_from_tape_event(_all_rates({
        "30s": (2, 20),
        "1m": (5, 25),
        "5m": (272, 41),
        "20m": (1363, 224),
    }))
    snap = tp.breadth_snapshot()
    assert snap.score > T_ENTRY
    assert snap.medium_breadth > 0
    assert snap.market_short_bearish()
    assert tp.allows_long_entry(snap)
    assert not tp.allows_short_entry(snap)


def test_bearish_medium_allows_short_not_long():
    tp = MarketThroughput()
    tp.update_from_tape_event(_all_rates({
        "30s": (2, 20),
        "1m": (2, 20),
        "5m": (10, 40),
        "20m": (50, 200),
    }))
    snap = tp.breadth_snapshot()
    assert snap.score < -T_ENTRY
    assert snap.medium_breadth < 0
    assert tp.allows_short_entry(snap)
    assert not tp.allows_long_entry(snap)


def test_v1_is_bearish_vs_long_legacy():
    tp = MarketThroughput()
    tp.update_from_tape_event(_all_rates({
        "30s": (2, 20),
        "1m": (5, 25),
        "5m": (272, 41),
        "20m": (1363, 224),
    }))
    assert not tp.is_bearish_vs_long(BREADTH_EDGE)


def test_format_snapshot_includes_breadth_scores():
    tp = MarketThroughput()
    tp.update_from_tape_event(_all_rates({"30s": (10, 2), "1m": (10, 2), "5m": (10, 2), "20m": (10, 2)}))
    text = tp.format_snapshot()
    assert "score=" in text
    assert "30s H10/L2" in text


def test_update_reads_flow_push_pull_from_roll_summary():
    tp = MarketThroughput()
    ev = {
        "event": "roll_window_summary",
        "roll_summary": {
            "windows_sec": [30, 60, 300, 1200],
            "high_avg": [0, 0, 0, 0],
            "low_avg": [0, 0, 0, 0],
            "panes": [{"highs": [], "lows": []} for _ in range(4)],
            "flow_push_pull": {"buy_pct_5m": 61.0, "sell_pct_5m": 39.0, "flow_events_5m": 47},
        },
    }
    tp.update_from_tape_event(ev)
    assert tp.flow_buy_pct == 61.0
    assert tp.flow_sell_pct == 39.0
    assert tp.flow_events == 47


def test_flow_state_unchanged_when_push_pull_absent():
    tp = MarketThroughput()
    tp.flow_buy_pct, tp.flow_sell_pct, tp.flow_events = 60.0, 40.0, 30
    tp.update_from_tape_event({"market_high_rate_30s": 5, "market_low_rate_30s": 1})
    assert tp.flow_buy_pct == 60.0  # untouched — no flow_push_pull on this event
    assert tp.flow_events == 30


def _set_flow(tp, buy, sell, events, **cfg):
    tp.flow_buy_pct, tp.flow_sell_pct, tp.flow_events = buy, sell, events
    tp.flow_mode = cfg.get("mode", "score")
    tp.flow_weight = cfg.get("weight", 0.5)
    tp.flow_edge = cfg.get("edge", 20.0)
    tp.flow_min_events = cfg.get("min_events", 10)


def test_flow_contribution_none_when_no_data():
    tp = MarketThroughput()
    _set_flow(tp, None, None, 0)
    assert tp.flow_contribution() == 0.0


def test_flow_contribution_zero_below_min_events():
    tp = MarketThroughput()
    _set_flow(tp, 90.0, 10.0, 5)  # strong split but only 5 events
    assert tp.flow_contribution() == 0.0


def test_flow_contribution_zero_inside_deadzone():
    tp = MarketThroughput()
    _set_flow(tp, 55.0, 45.0, 50)  # net 10 < edge 20
    assert tp.flow_contribution() == 0.0


def test_flow_contribution_scales_signed_net():
    tp = MarketThroughput()
    _set_flow(tp, 80.0, 20.0, 50)  # net +60, weight 0.5
    assert tp.flow_contribution() == 30.0
    _set_flow(tp, 20.0, 80.0, 50)  # net -60
    assert tp.flow_contribution() == -30.0


def test_flow_contribution_off_mode_is_zero():
    tp = MarketThroughput()
    _set_flow(tp, 90.0, 10.0, 50, mode="off")
    assert tp.flow_contribution() == 0.0


def test_flow_folds_into_score_and_medium_not_short():
    tp = MarketThroughput()
    tp.update_from_tape_event(_all_rates({
        "30s": (10, 4), "1m": (12, 5), "5m": (40, 20), "20m": (100, 60),
    }))
    base = tp.breadth_snapshot()
    _set_flow(tp, 80.0, 20.0, 50)  # +30 flow term
    snap = tp.breadth_snapshot()
    assert snap.contributions["flow"] == 30.0
    assert snap.score == base.score + 30.0
    assert snap.medium_breadth == base.medium_breadth + 30.0
    assert snap.short_breadth == base.short_breadth  # unchanged


def test_format_scores_includes_flow():
    tp = MarketThroughput()
    tp.update_from_tape_event(_all_rates({w: (10, 4) for w in ("30s", "1m", "5m", "20m")}))
    _set_flow(tp, 80.0, 20.0, 50)
    text = tp.breadth_snapshot().format_scores()
    assert "flow=+30" in text
