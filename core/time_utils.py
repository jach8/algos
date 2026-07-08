from __future__ import annotations

import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

EASTERN_TZ = ZoneInfo("America/New_York")


def iso_now() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def parse_iso(ts: str) -> float:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return time.time()


def fmt_est(ts_sec: float) -> str:
    dt = datetime.fromtimestamp(ts_sec, tz=EASTERN_TZ)
    return dt.strftime("%-m/%-d %I:%M:%S %p %Z")


def now_est() -> str:
    return fmt_est(time.time())


def event_est(ev: dict) -> str:
    ts = ev.get("timestamp")
    if isinstance(ts, str):
        return fmt_est(parse_iso(ts))
    return now_est()
