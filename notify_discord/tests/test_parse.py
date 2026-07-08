from __future__ import annotations

from notify_discord.notify import parse_readings


def ev(**kw) -> dict:
    base = {
        "type": "TAPE_EVENT",
        "symbol": "AAPL",
        "event": "new_high",
        "high_count": 7,
        "low_count": 2,
        "last_price": 187.5,
        "pct_change": 1.3,
        "volume_spike": False,
    }
    base.update(kw)
    return base


def test_new_high_yields_one_high_reading():
    rs = parse_readings(ev(event="new_high", high_count=7))
    assert len(rs) == 1
    r = rs[0]
    assert r.symbol == "AAPL"
    assert r.side == "high"
    assert r.count == 7
    assert r.last_price == 187.5
    assert r.pct_change == 1.3
    assert r.volume_spike is False


def test_new_low_yields_one_low_reading():
    rs = parse_readings(ev(event="new_low", low_count=4))
    assert len(rs) == 1
    assert rs[0].side == "low"
    assert rs[0].count == 4


def test_new_high_and_low_yields_both_readings():
    rs = parse_readings(ev(event="new_high_and_low", high_count=9, low_count=5))
    sides = {r.side: r.count for r in rs}
    assert sides == {"high": 9, "low": 5}


def test_price_update_yields_nothing():
    assert parse_readings(ev(event="price_update")) == []


def test_market_summary_yields_nothing():
    assert parse_readings(ev(event="market_summary", symbol="-")) == []


def test_non_tape_event_yields_nothing():
    assert parse_readings({"type": "ALGO_HEARTBEAT"}) == []


def test_volume_spike_passthrough():
    rs = parse_readings(ev(event="new_high", volume_spike=True))
    assert rs[0].volume_spike is True
