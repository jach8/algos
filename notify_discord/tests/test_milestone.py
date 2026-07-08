from __future__ import annotations

from notify_discord.notify import MilestoneGate, format_alert


def gate() -> MilestoneGate:
    # first alert at 5, then every 5, 60s per-symbol cooldown
    return MilestoneGate(milestone=5, step=5, cooldown_secs=60)


def test_below_milestone_does_not_fire():
    g = gate()
    assert g.should_fire("AAPL", "high", count=3, now=0.0) is False


def test_fires_at_first_milestone():
    g = gate()
    assert g.should_fire("AAPL", "high", count=5, now=0.0) is True


def test_does_not_refire_between_milestones():
    g = gate()
    assert g.should_fire("AAPL", "high", count=5, now=0.0) is True
    assert g.should_fire("AAPL", "high", count=7, now=100.0) is False


def test_fires_at_next_milestone():
    g = gate()
    assert g.should_fire("AAPL", "high", count=5, now=0.0) is True
    assert g.should_fire("AAPL", "high", count=10, now=100.0) is True


def test_count_jump_fires_once_for_highest_crossed():
    g = gate()
    assert g.should_fire("AAPL", "high", count=5, now=0.0) is True
    # count leaps 5 -> 12 (crosses the 10 milestone); fires once
    assert g.should_fire("AAPL", "high", count=12, now=100.0) is True
    # 12 is past the 10 milestone but not yet 15; no refire
    assert g.should_fire("AAPL", "high", count=13, now=200.0) is False


def test_cooldown_suppresses_rapid_second_fire():
    g = gate()
    assert g.should_fire("NVDA", "high", count=5, now=0.0) is True
    # next milestone reached only 30s later -> within 60s cooldown -> suppressed
    assert g.should_fire("NVDA", "high", count=10, now=30.0) is False
    # after cooldown, the still-pending milestone fires
    assert g.should_fire("NVDA", "high", count=10, now=70.0) is True


def test_symbols_are_independent():
    g = gate()
    assert g.should_fire("AAPL", "high", count=5, now=0.0) is True
    assert g.should_fire("NVDA", "high", count=5, now=0.0) is True


def test_sides_are_independent():
    g = gate()
    assert g.should_fire("AAPL", "high", count=5, now=0.0) is True
    assert g.should_fire("AAPL", "low", count=5, now=0.0) is True


def test_format_alert_high_embed():
    payload = format_alert(
        symbol="NVDA", side="high", count=10,
        last_price=123.45, pct_change=4.2, volume_spike=False,
    )
    embed = payload["embeds"][0]
    assert "NVDA" in embed["title"]
    assert "10th new high" in embed["title"]
    assert "⚡" not in embed["title"]  # no volume-spike bolt


def test_format_alert_low_with_volume_spike():
    payload = format_alert(
        symbol="SPY", side="low", count=15,
        last_price=500.0, pct_change=-1.1, volume_spike=True,
    )
    embed = payload["embeds"][0]
    assert "15th new low" in embed["title"]
    assert "⚡" in embed["title"]  # volume-spike bolt present
