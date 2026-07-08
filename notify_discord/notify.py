"""Discord momentum notifier for the HighLowTicker algo feed.

Connects to the local algo-feed WebSocket and posts a Discord alert when a
symbol *repeatedly* prints new session highs/lows (a momentum signal), keyed off
the feed's own ``high_count`` / ``low_count`` — not every single new high/low.

Run:  DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... \\
      .venv/bin/python -m notify_discord.notify
See README for all env vars. All decision logic lives in the pure, unit-tested
functions below; the async loop is a thin I/O shell around them.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass
class _State:
    last_milestone: int
    last_ts: float


@dataclass
class Reading:
    symbol: str
    side: str  # "high" | "low"
    count: int
    last_price: float | None
    pct_change: float | None
    volume_spike: bool


def parse_readings(ev: dict) -> list[Reading]:
    """Extract 0..2 momentum readings from a raw feed frame.

    ``new_high`` -> one high reading, ``new_low`` -> one low reading,
    ``new_high_and_low`` -> both. Everything else (price_update,
    market_summary, non-tape frames) -> ``[]``.
    """
    if ev.get("type") != "TAPE_EVENT":
        return []
    event = ev.get("event")
    symbol = ev.get("symbol")
    last_price = ev.get("last_price")
    pct_change = ev.get("pct_change")
    volume_spike = bool(ev.get("volume_spike", False))

    readings: list[Reading] = []
    if event in ("new_high", "new_high_and_low"):
        readings.append(Reading(symbol, "high", ev.get("high_count"),
                                last_price, pct_change, volume_spike))
    if event in ("new_low", "new_high_and_low"):
        readings.append(Reading(symbol, "low", ev.get("low_count"),
                                last_price, pct_change, volume_spike))
    return readings


class MilestoneGate:
    """Decides WHEN a per-symbol count crosses an alert milestone.

    Fires the first time a (symbol, side) count reaches ``milestone``, then once
    each time it crosses a further ``step``. A per-symbol ``cooldown_secs``
    absorbs bursts (a suppressed milestone stays pending and fires once the
    cooldown clears). Stateful and deterministic: pass ``now`` explicitly.
    """

    def __init__(self, milestone: int, step: int, cooldown_secs: float) -> None:
        self.milestone = milestone
        self.step = step
        self.cooldown_secs = cooldown_secs
        self._state: Dict[Tuple[str, str], _State] = {}

    def should_fire(self, symbol: str, side: str, count: int, now: float) -> bool:
        if count < self.milestone:
            return False
        # Highest milestone level at/below the current count.
        level = self.milestone + ((count - self.milestone) // self.step) * self.step
        key = (symbol, side)
        st = self._state.get(key)
        if st is not None and level <= st.last_milestone:
            return False  # not a new milestone
        if st is not None and (now - st.last_ts) < self.cooldown_secs:
            return False  # within cooldown: leave pending, do not advance
        self._state[key] = _State(last_milestone=level, last_ts=now)
        return True


def _ordinal(n: int) -> str:
    if 10 <= (n % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def format_alert(
    symbol: str,
    side: str,
    count: int,
    last_price: float | None,
    pct_change: float | None,
    volume_spike: bool,
) -> dict:
    """Build the Discord webhook JSON payload for one milestone alert."""
    arrow = "🔼" if side == "high" else "🔽"
    kind = "new high" if side == "high" else "new low"
    bolt = " ⚡" if volume_spike else ""
    title = f"{arrow} {symbol} · {_ordinal(count)} {kind}{bolt}"

    fields = []
    if last_price is not None:
        fields.append({"name": "Last", "value": f"${last_price:,.2f}", "inline": True})
    if pct_change is not None:
        fields.append({"name": "Change", "value": f"{pct_change:+.2f}%", "inline": True})

    color = 0x22C55E if side == "high" else 0xEF4444  # green / red
    return {"embeds": [{"title": title, "color": color, "fields": fields}]}


# --------------------------------------------------------------------------- #
# I/O shell  (thin glue around the tested pure functions above)               #
# --------------------------------------------------------------------------- #

RECONNECT_DELAY = 5.0


@dataclass
class Config:
    webhook_url: str
    ws_url: str
    watch: set  # empty = all symbols
    sides: set  # subset of {"high", "low"}
    milestone: int
    step: int
    cooldown_secs: float


def load_config(env: dict | None = None) -> Config:
    env = os.environ if env is None else env
    webhook = env.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        raise SystemExit("DISCORD_WEBHOOK_URL is required")

    raw_sides = env.get("HLT_SIDES", "both").strip().lower()
    sides = {"high", "low"} if raw_sides == "both" else {
        "highs": {"high"}, "lows": {"low"}, "high": {"high"}, "low": {"low"},
    }.get(raw_sides, {"high", "low"})

    watch = {s.strip().upper() for s in env.get("HLT_WATCH", "").split(",") if s.strip()}
    return Config(
        webhook_url=webhook,
        ws_url=env.get("HLT_ALGO_WS", "ws://127.0.0.1:7412").strip(),
        watch=watch,
        sides=sides,
        milestone=int(env.get("HLT_MILESTONE", "5")),
        step=int(env.get("HLT_STEP", "5")),
        cooldown_secs=float(env.get("HLT_COOLDOWN_SECS", "60")),
    )


def _post_webhook(url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            # Discord sits behind Cloudflare, which 403s the default
            # "Python-urllib/x.y" User-Agent. A custom UA is required.
            "User-Agent": "HLT-Notifier/1.0 (+https://highlowtick.com)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (trusted user URL)
        resp.read()


async def run(cfg: Config) -> None:
    import websockets  # local import so the pure functions need no ws dependency

    gate = MilestoneGate(cfg.milestone, cfg.step, cfg.cooldown_secs)
    watch_label = ",".join(sorted(cfg.watch)) if cfg.watch else "ALL"
    loop = asyncio.get_event_loop()
    while True:
        try:
            print(f"[notify] connecting to {cfg.ws_url} (watch={watch_label}, "
                  f"sides={'/'.join(sorted(cfg.sides))}, "
                  f"milestone={cfg.milestone} step={cfg.step}) …", flush=True)
            async with websockets.connect(cfg.ws_url, ping_interval=20, ping_timeout=120) as ws:
                async for raw in ws:
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    for r in parse_readings(ev):
                        if r.side not in cfg.sides:
                            continue
                        if cfg.watch and r.symbol not in cfg.watch:
                            continue
                        if r.count is None:
                            continue
                        if gate.should_fire(r.symbol, r.side, r.count, time.monotonic()):
                            payload = format_alert(
                                r.symbol, r.side, r.count,
                                r.last_price, r.pct_change, r.volume_spike,
                            )
                            try:
                                await loop.run_in_executor(
                                    None, _post_webhook, cfg.webhook_url, payload
                                )
                                print(f"[notify] {r.symbol} {r.side} #{r.count} -> Discord",
                                      flush=True)
                            except Exception as e:  # don't let a webhook hiccup kill the feed
                                print(f"[notify] webhook error: {e}", file=sys.stderr, flush=True)
        except OSError as e:
            print(f"[notify] disconnect: {e}; retrying in {RECONNECT_DELAY}s",
                  file=sys.stderr, flush=True)
            await asyncio.sleep(RECONNECT_DELAY)


def main() -> None:
    cfg = load_config()
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        print("\n[notify] stopped", flush=True)


if __name__ == "__main__":
    main()
