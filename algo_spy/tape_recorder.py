"""Record TAPE_EVENT throughput + SPY ticks for session replay / backtests."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")

MARKET_RATE_KEYS: tuple[str, ...] = (
    "market_high_rate_30s",
    "market_low_rate_30s",
    "market_high_rate_1m",
    "market_low_rate_1m",
    "market_high_rate_5m",
    "market_low_rate_5m",
    "market_high_rate_20m",
    "market_low_rate_20m",
)

SPY_PRICE_KEYS: tuple[str, ...] = (
    "last_price",
    "session_high",
    "session_low",
    "high_count",
    "low_count",
    "bid",
    "ask",
)

DEFAULT_TAPE_DIR = Path(__file__).resolve().parent / "sessions"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    raw = raw.split("#", 1)[0].strip().lower()
    return raw in ("1", "true", "yes", "on")


def tape_dir() -> Path:
    raw = os.environ.get("ALGO_SPY_TAPE_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_TAPE_DIR


def recording_enabled() -> bool:
    return _env_bool("ALGO_SPY_RECORD_TAPE", True)


def extract_replay_record(ev: dict) -> dict:
    """Slim TAPE_EVENT row — enough to rebuild throughput + SPY price path."""
    row: dict = {
        "ts": ev.get("ts"),
        "symbol": ev.get("symbol"),
        "event": ev.get("event"),
    }
    for key in MARKET_RATE_KEYS:
        if key in ev:
            row[key] = ev[key]
    for key in SPY_PRICE_KEYS:
        if key in ev and ev[key] is not None:
            row[key] = ev[key]
    if ev.get("event") == "roll_window_summary" and ev.get("roll_summary") is not None:
        row["roll_summary"] = ev["roll_summary"]
    return row


def row_to_tape_event(row: dict) -> dict:
    """Rebuild a TAPE_EVENT dict for strategy dispatch."""
    return {"type": "TAPE_EVENT", **row}


class TapeRecorder:
    """Append-only session tape log (one file per ET session day)."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or tape_dir()
        self.path: Path | None = None
        self._count = 0

    def reset(self, *, session_date: str | None = None) -> Path | None:
        if not recording_enabled():
            self.path = None
            self._count = 0
            return None
        if session_date is None:
            session_date = datetime.now(TZ).strftime("%Y%m%d")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / f"{session_date}_tape.jsonl"
        self.path.write_text("")
        meta = self.directory / f"{session_date}_meta.json"
        meta.write_text(
            json.dumps(
                {
                    "session_date": session_date,
                    "started_at": datetime.now(TZ).isoformat(),
                    "format": "tape_replay_v1",
                },
                indent=2,
            )
            + "\n"
        )
        self._count = 0
        return self.path

    def record(self, ev: dict) -> None:
        if self.path is None or ev.get("type") != "TAPE_EVENT":
            return
        row = extract_replay_record(ev)
        if not row.get("ts"):
            return
        with self.path.open("a") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
        self._count += 1

    def finalize(self) -> None:
        if self.path is None:
            return
        meta_path = self.path.with_name(self.path.stem.replace("_tape", "_meta") + ".json")
        if meta_path.is_file():
            data = json.loads(meta_path.read_text())
            data["events"] = self._count
            data["finished_at"] = datetime.now(TZ).isoformat()
            data["tape_file"] = self.path.name
            meta_path.write_text(json.dumps(data, indent=2) + "\n")


def load_tape_log(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: r.get("ts") or "")
    return rows


def latest_tape_log(directory: Path | None = None) -> Path | None:
    base = directory or tape_dir()
    if not base.is_dir():
        return None
    files = sorted(base.glob("*_tape.jsonl"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None
