"""Build labeled entry candidates from recorded tapes (causal features + forward label)."""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from pathlib import Path

from core.time_utils import parse_iso

from ..bars import BAR_5M_SEC, BarAggregator, EmaStack
from ..tape_recorder import load_tape_log, row_to_tape_event
from ..throughput import MarketThroughput
from .costs import round_trip_cost_per_share
from .features import candidate_features

LABEL_HORIZON_SEC = 1200.0


@dataclass
class Candidate:
    session: str
    ts_sec: float
    close: float
    side: int
    features: list[float]
    label: int | None = None
    net_return: float | None = None


def _session_of(path: Path) -> str:
    return path.stem.replace("_tape", "")


def build_candidates_for_tape(
    path: Path, *, label_horizon_sec: float = LABEL_HORIZON_SEC
) -> list[Candidate]:
    rows = load_tape_log(path)
    session = _session_of(path)
    throughput = MarketThroughput()
    ema = EmaStack()
    bars = BarAggregator(period_sec=BAR_5M_SEC)
    price_ts: list[float] = []
    price_px: list[float] = []
    candidates: list[Candidate] = []

    for row in rows:
        ev = row_to_tape_event(row)
        ts = ev.get("ts") or ""
        if not ts:
            continue
        ts_sec = parse_iso(ts)
        throughput.update_from_tape_event(ev)
        if ev.get("symbol") != "SPY":
            continue
        price = ev.get("last_price")
        if price is None:
            continue
        price = float(price)
        price_ts.append(ts_sec)
        price_px.append(price)
        bars.on_tick(ts_sec, price)
        for bar in bars.pop_closed():
            ema.update(bar.close)
            if not ema.ready():
                continue
            ema1, _, ema3 = ema.levels()
            assert ema1 is not None and ema3 is not None
            snap = throughput.breadth_snapshot()
            bar_ts = bar.bucket_epoch * BAR_5M_SEC + BAR_5M_SEC
            if throughput.allows_long_entry(snap):
                candidates.append(
                    Candidate(
                        session,
                        bar_ts,
                        bar.close,
                        1,
                        candidate_features(
                            breadth=snap,
                            close=bar.close,
                            ema1=ema1,
                            ema3=ema3,
                            side=1,
                            ts_sec=bar_ts,
                        ),
                    )
                )
            if throughput.allows_short_entry(snap):
                candidates.append(
                    Candidate(
                        session,
                        bar_ts,
                        bar.close,
                        -1,
                        candidate_features(
                            breadth=snap,
                            close=bar.close,
                            ema1=ema1,
                            ema3=ema3,
                            side=-1,
                            ts_sec=bar_ts,
                        ),
                    )
                )

    _attach_labels(candidates, price_ts, price_px, label_horizon_sec)
    return candidates


def _price_at_or_after(
    price_ts: list[float], price_px: list[float], target_ts: float
) -> float | None:
    idx = bisect.bisect_left(price_ts, target_ts)
    if idx >= len(price_px):
        return None
    return price_px[idx]


def _attach_labels(
    candidates: list[Candidate],
    price_ts: list[float],
    price_px: list[float],
    horizon_sec: float,
) -> None:
    if not price_ts:
        return
    last_ts = price_ts[-1]
    for c in candidates:
        future_ts = c.ts_sec + horizon_sec
        if future_ts > last_ts:
            continue  # incomplete forward window — drop (label stays None)
        future_px = _price_at_or_after(price_ts, price_px, future_ts)
        if future_px is None:
            continue
        gross = (future_px - c.close) * c.side
        net = gross - round_trip_cost_per_share(c.close)
        c.net_return = net
        c.label = 1 if net > 0 else 0


def build_all(
    paths: list[Path], *, label_horizon_sec: float = LABEL_HORIZON_SEC
) -> list[Candidate]:
    out: list[Candidate] = []
    for p in sorted(paths, key=lambda x: _session_of(x)):
        out.extend(build_candidates_for_tape(p, label_horizon_sec=label_horizon_sec))
    return out
