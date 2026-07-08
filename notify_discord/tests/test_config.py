from __future__ import annotations

import pytest

from notify_discord.notify import load_config


def test_missing_webhook_raises():
    with pytest.raises(SystemExit):
        load_config({})


def test_defaults():
    cfg = load_config({"DISCORD_WEBHOOK_URL": "http://x"})
    assert cfg.ws_url == "ws://127.0.0.1:7412"
    assert cfg.watch == set()
    assert cfg.sides == {"high", "low"}
    assert cfg.milestone == 5
    assert cfg.step == 5
    assert cfg.cooldown_secs == 60.0


def test_sides_highs_only():
    cfg = load_config({"DISCORD_WEBHOOK_URL": "http://x", "HLT_SIDES": "highs"})
    assert cfg.sides == {"high"}


def test_sides_lows_only():
    cfg = load_config({"DISCORD_WEBHOOK_URL": "http://x", "HLT_SIDES": "lows"})
    assert cfg.sides == {"low"}


def test_unknown_sides_falls_back_to_both():
    cfg = load_config({"DISCORD_WEBHOOK_URL": "http://x", "HLT_SIDES": "sideways"})
    assert cfg.sides == {"high", "low"}


def test_watch_parsed_uppercased_and_deduped():
    cfg = load_config({"DISCORD_WEBHOOK_URL": "http://x", "HLT_WATCH": "aapl, nvda ,aapl"})
    assert cfg.watch == {"AAPL", "NVDA"}


def test_overrides():
    cfg = load_config({
        "DISCORD_WEBHOOK_URL": "http://x",
        "HLT_ALGO_WS": "ws://10.0.0.2:9000",
        "HLT_MILESTONE": "3",
        "HLT_STEP": "2",
        "HLT_COOLDOWN_SECS": "15",
    })
    assert cfg.ws_url == "ws://10.0.0.2:9000"
    assert (cfg.milestone, cfg.step, cfg.cooldown_secs) == (3, 2, 15.0)
