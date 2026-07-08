"""Causal feature vector for the entry filter (everything known at 5m bar close)."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ..throughput import BreadthSnapshot

TZ_ET = ZoneInfo("America/New_York")

FEATURE_NAMES: tuple[str, ...] = (
    "score",
    "short_breadth",
    "medium_breadth",
    "flow",
    "ema1_minus_ema3_bps",
    "close_minus_ema3_bps",
    "side",            # +1 long candidate, -1 short candidate
    "minutes_since_open",
)

# Causal features for oracle-imitation (no side — the model predicts direction).
ORACLE_FEATURE_NAMES: tuple[str, ...] = (
    "score",
    "short_breadth",
    "medium_breadth",
    "flow",
    "ema1_minus_ema3_bps",
    "close_minus_ema3_bps",
    "minutes_since_open",
)


def bar_features(
    *,
    breadth: BreadthSnapshot,
    close: float,
    ema1: float,
    ema3: float,
    ts_sec: float,
) -> list[float]:
    dt = datetime.fromtimestamp(ts_sec, tz=TZ_ET)
    minutes_since_open = (dt.hour * 60 + dt.minute) - (9 * 60 + 30)
    ema1_minus_ema3_bps = (ema1 - ema3) / close * 10_000.0 if close else 0.0
    close_minus_ema3_bps = (close - ema3) / close * 10_000.0 if close else 0.0
    return [
        breadth.score,
        breadth.short_breadth,
        breadth.medium_breadth,
        breadth.contributions.get("flow", 0.0),
        ema1_minus_ema3_bps,
        close_minus_ema3_bps,
        float(minutes_since_open),
    ]


def candidate_features(
    *,
    breadth: BreadthSnapshot,
    close: float,
    ema1: float,
    ema3: float,
    side: int,
    ts_sec: float,
) -> list[float]:
    dt = datetime.fromtimestamp(ts_sec, tz=TZ_ET)
    minutes_since_open = (dt.hour * 60 + dt.minute) - (9 * 60 + 30)
    ema1_minus_ema3_bps = (ema1 - ema3) / close * 10_000.0 if close else 0.0
    close_minus_ema3_bps = (close - ema3) / close * 10_000.0 if close else 0.0
    return [
        breadth.score,
        breadth.short_breadth,
        breadth.medium_breadth,
        breadth.contributions.get("flow", 0.0),
        ema1_minus_ema3_bps,
        close_minus_ema3_bps,
        float(side),
        float(minutes_since_open),
    ]
