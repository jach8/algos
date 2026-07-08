from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable

import websockets
from websockets.exceptions import ConnectionClosed

from .time_utils import iso_now, now_est

RECONNECT_DELAY = 5.0
HEARTBEAT_S = 30.0
PNL_REPORT_S = 15.0


def watch_payload(symbols: list[str], algo_id: str) -> str:
    # v1.0.2 ingress: the binary accepts SUBSCRIBE_WATCH { symbols } only. The old
    # ALGO_CUSTOM/ALGO_WEB_WATCH frame silently no-ops (debug-logged, dropped), which
    # cost this subscription its price_update ticks. algo_id is kept in the signature
    # for call-site compatibility but is no longer part of the wire frame.
    syms = [s.strip().upper() for s in symbols if s and str(s).strip()]
    return json.dumps({
        "type": "SUBSCRIBE_WATCH",
        "symbols": syms,
    })


def heartbeat_payload(algo_id: str) -> str:
    return json.dumps({
        "type": "ALGO_HEARTBEAT",
        "algo_id": algo_id,
        "timestamp": iso_now(),
    })


def pnl_payload(strat, algo_id: str) -> str:
    metrics_fn = getattr(strat, "pnl_metrics", None)
    if callable(metrics_fn):
        realized, unrealized, open_positions = metrics_fn()
    else:
        pos = strat.account.position
        last = strat.last_price or (pos.entry_price if pos else 0.0)
        realized = strat.account.realized_pnl()
        unrealized = 0.0 if pos is None else (last - pos.entry_price) * pos.qty
        open_positions = 0 if pos is None else 1
    return json.dumps({
        "type": "ALGO_PNL",
        "algo_id": algo_id,
        "realized": realized,
        "unrealized": unrealized,
        "open_positions": open_positions,
        "timestamp": iso_now(),
    })


async def run_feed_loop(
    *,
    url: str,
    symbol: str,
    algo_label: str,
    algo_id: str,
    strat,
    on_tape_event: Callable[[dict], list[dict]],
    on_emit: Callable[[dict], None],
    watch_symbols: list[str] | None = None,
    after_tape_event: Callable[[object, object], Awaitable[None]] | None = None,
    quiet: bool = False,
) -> None:
    initial_watch = watch_symbols if watch_symbols is not None else [symbol]
    while True:
        try:
            if not quiet:
                print(f"[{algo_label} {now_est()}] connecting to {url} …")
            async with websockets.connect(url, ping_interval=20, ping_timeout=120) as ws:
                await ws.send(watch_payload(initial_watch, algo_id))
                if not quiet:
                    watch_label = ",".join(initial_watch) if initial_watch else "(roll-window only)"
                    print(f"[{algo_label} {now_est()}] watching [{watch_label}]")

                stop = asyncio.Event()

                async def periodic() -> None:
                    last_hb = 0.0
                    last_pnl = 0.0
                    while not stop.is_set():
                        try:
                            await asyncio.wait_for(stop.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            pass
                        now = time.monotonic()
                        if now - last_hb >= HEARTBEAT_S:
                            await ws.send(heartbeat_payload(algo_id))
                            last_hb = now
                        if now - last_pnl >= PNL_REPORT_S:
                            await ws.send(pnl_payload(strat, algo_id))
                            last_pnl = now

                pump = asyncio.create_task(periodic())
                try:
                    async for raw in ws:
                        try:
                            ev = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if ev.get("type") != "TAPE_EVENT":
                            continue
                        for out in on_tape_event(ev):
                            await ws.send(json.dumps(out))
                            on_emit(out)
                        if after_tape_event is not None:
                            await after_tape_event(ws, strat)
                finally:
                    stop.set()
                    pump.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await pump
        except (ConnectionClosed, OSError) as e:
            if not quiet:
                print(f"[{algo_label} {now_est()}] disconnect: {e}; retrying in {RECONNECT_DELAY}s")
            await asyncio.sleep(RECONNECT_DELAY)
