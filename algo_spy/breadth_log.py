"""Session breadth time series for replay charts."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .strategy import Strategy

DEFAULT_BREADTH_LOG = Path(__file__).resolve().parent / "breadth.jsonl"


@dataclass
class BreadthSample:
    ts: str
    ts_sec: float
    score: float
    short_breadth: float
    medium_breadth: float
    divergence: str
    kind: str = "tape"


class BreadthLogger:
    """Append breadth samples during live sessions (for replay overlay)."""

    def __init__(self, path: Path = DEFAULT_BREADTH_LOG, min_interval_sec: float = 5.0) -> None:
        self.path = path
        self.min_interval_sec = min_interval_sec
        self._last_ts_sec = -1e18

    def reset(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("")
        self._last_ts_sec = -1e18

    def maybe_log(
        self,
        strat: "Strategy",
        ts_sec: float,
        ts: str,
        *,
        kind: str = "tape",
        force: bool = False,
    ) -> None:
        if not force and ts_sec - self._last_ts_sec < self.min_interval_sec:
            return
        self._last_ts_sec = ts_sec
        breadth = strat.last_breadth or strat.throughput.breadth_snapshot()
        row = BreadthSample(
            ts=ts,
            ts_sec=ts_sec,
            score=breadth.score,
            short_breadth=breadth.short_breadth,
            medium_breadth=breadth.medium_breadth,
            divergence=strat.last_divergence,
            kind=kind,
        )
        with self.path.open("a") as f:
            f.write(json.dumps(asdict(row)) + "\n")


def load_breadth_log(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
