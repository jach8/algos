"""Minimal HighLowTicker strategy — the read-it-in-one-sitting version.

The strategy analog of notify_simple.py. It shows the FULL algo-feed loop that a
notifier can't: read TAPE_EVENT frames (egress), make a decision, and emit
ALGO_SIGNAL / ALGO_ORDER frames back (ingress) — while keeping a tiny in-memory
paper account so you can watch a running result.

Strategy (deliberately trivial, momentum-fade — a teaching example, NOT profitable):
  - Go long 1 unit the first time the symbol prints its Nth new session high.
  - Close the position when it prints a new session low.

Run:  .venv/bin/python strategy_simple.py          # from algos/
      .venv/bin/python -m algos.strategy_simple     # from repo root
Env:  HLT_ALGO_WS (default ws://127.0.0.1:7412), HLT_SYMBOL (default SPY),
      HLT_MILESTONE (default 5).
"""
import asyncio
import json
import os
from datetime import datetime, timezone

import websockets

WS_URL = os.environ.get("HLT_ALGO_WS", "ws://127.0.0.1:7412").strip()
SYMBOL = os.environ.get("HLT_SYMBOL", "SPY").strip().upper()
MILESTONE = int(os.environ.get("HLT_MILESTONE", "5"))  # enter on the Nth new high
ALGO_ID = "strategy_simple"


def _now() -> str:
    """ISO-8601 UTC timestamp, e.g. 2026-07-01T14:03:12.482Z."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def _send(ws, frame: dict) -> None:
    """Emit one ingress frame back to HLT (JSON text)."""
    await ws.send(json.dumps(frame))


async def main() -> None:
    # Tiny in-memory paper account.
    position = 0        # units held (0 or 1)
    entry_price = 0.0
    realized = 0.0
    last_price = None

    print(f"[strategy] {SYMBOL}: long on new high #{MILESTONE}, exit on a new low", flush=True)
    async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=120) as ws:
        # Subscribe so we also receive price_update ticks for this symbol (an ingress verb).
        await _send(ws, {"type": "ALGO_CUSTOM", "tag": "ALGO_WEB_WATCH",
                         "timestamp": _now(), "data": {"symbols": [SYMBOL]}})

        async for raw in ws:
            ev = json.loads(raw)
            if ev.get("type") != "TAPE_EVENT" or ev.get("symbol") != SYMBOL:
                continue

            event = ev.get("event", "")
            if ev.get("last_price") is not None:
                last_price = ev["last_price"]

            # ENTRY: flat + Nth new high -> go long, report a signal + an order.
            if position == 0 and "high" in event and (ev.get("high_count") or 0) >= MILESTONE \
                    and last_price is not None:
                position, entry_price = 1, last_price
                await _send(ws, {"type": "ALGO_SIGNAL", "symbol": SYMBOL, "side": "BUY",
                                 "strength": 1.0, "reason": f"new_high_{ev.get('high_count')}",
                                 "algo_id": ALGO_ID, "timestamp": _now()})
                await _send(ws, {"type": "ALGO_ORDER", "symbol": SYMBOL, "side": "BUY",
                                 "size": 1, "price": entry_price,
                                 "algo_id": ALGO_ID, "timestamp": _now()})
                print(f"[strategy] ENTER long {SYMBOL} @ {entry_price:.2f}", flush=True)

            # EXIT: long + new low -> close, realize P/L, report the closing order.
            elif position == 1 and "low" in event and last_price is not None:
                realized += last_price - entry_price
                await _send(ws, {"type": "ALGO_ORDER", "symbol": SYMBOL, "side": "SELL",
                                 "size": 1, "price": last_price,
                                 "algo_id": ALGO_ID, "timestamp": _now()})
                print(f"[strategy] EXIT  {SYMBOL} @ {last_price:.2f}  "
                      f"realized {realized:+.2f}", flush=True)
                position, entry_price = 0, 0.0

            # Running mark-to-market while long (only on price ticks, to stay quiet).
            elif position == 1 and event == "price_update" and last_price is not None:
                print(f"[strategy] hold {SYMBOL} entry {entry_price:.2f} last {last_price:.2f} "
                      f"unrealized {last_price - entry_price:+.2f}", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[strategy] stopped", flush=True)
