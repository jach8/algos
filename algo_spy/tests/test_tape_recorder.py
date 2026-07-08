from __future__ import annotations

import json
from pathlib import Path

import pytest

from algo_spy.main import handle_tape_event
from algo_spy.strategy import Strategy
from algo_spy.tape_recorder import (
    TapeRecorder,
    extract_replay_record,
    load_tape_log,
    row_to_tape_event,
)
from algo_spy.throughput import MarketThroughput


def test_extract_replay_record_keeps_rates_and_spy_price():
    ev = {
        "type": "TAPE_EVENT",
        "ts": "2026-05-29T16:05:00.000Z",
        "symbol": "SPY",
        "event": "new_high",
        "last_price": 756.98,
        "market_high_rate_30s": 4,
        "market_low_rate_30s": 2,
        "market_high_rate_1m": 15,
        "market_low_rate_1m": 21,
        "market_high_rate_5m": 96,
        "market_low_rate_5m": 87,
        "market_high_rate_20m": 726,
        "market_low_rate_20m": 273,
    }
    row = extract_replay_record(ev)
    back = row_to_tape_event(row)
    tp = MarketThroughput()
    tp.update_from_tape_event(back)
    snap = tp.breadth_snapshot()
    assert snap.score > 0
    assert back["last_price"] == 756.98


def test_tape_recorder_roundtrip(tmp_path: Path):
    rec = TapeRecorder(directory=tmp_path)
    path = rec.reset(session_date="20260529")
    assert path is not None
    ev = {
        "type": "TAPE_EVENT",
        "ts": "2026-05-29T16:05:00.000Z",
        "symbol": "SPY",
        "event": "price_update",
        "last_price": 100.0,
        "market_high_rate_30s": 10,
        "market_low_rate_30s": 2,
        "market_high_rate_1m": 20,
        "market_low_rate_1m": 4,
        "market_high_rate_5m": 50,
        "market_low_rate_5m": 10,
        "market_high_rate_20m": 200,
        "market_low_rate_20m": 40,
    }
    rec.record(ev)
    rec.finalize()
    rows = load_tape_log(path)
    assert len(rows) == 1
    assert rows[0]["last_price"] == 100.0


def test_replay_feeds_throughput(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("algo_spy.strategy.commission_for_shares", lambda _qty: 0.0)
    monkeypatch.setattr("algo_spy.execution.slippage_fraction", lambda: 0.0)
    strat = Strategy()
    row = {
        "ts": "2026-05-29T16:05:00.000Z",
        "symbol": "SPY",
        "event": "price_update",
        "last_price": 756.0,
        "market_high_rate_30s": 21,
        "market_low_rate_30s": 6,
        "market_high_rate_1m": 36,
        "market_low_rate_1m": 8,
        "market_high_rate_5m": 272,
        "market_low_rate_5m": 41,
        "market_high_rate_20m": 1363,
        "market_low_rate_20m": 224,
    }
    handle_tape_event(strat, row_to_tape_event(row), record_tape=False, log_breadth=False)
    assert strat.last_breadth is not None
    assert strat.last_breadth.score > 0
    assert strat.last_price == 756.0
