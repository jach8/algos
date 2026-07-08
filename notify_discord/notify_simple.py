"""Discord momentum notifier — the simple, read-it-in-one-sitting version.

Same core idea as notify.py, stripped to the essentials so you can see how the
whole thing works at a glance: connect to the HighLowTicker algo feed, and post a
Discord alert the first time a symbol reaches its Nth new session high/low (a
momentum signal). It keys off the feed's own high_count / low_count, so you get
ONE ping when a name gets hot — not a message on every single new high.

What this leaves out (see notify.py for the full version): watchlist/side filters,
re-alerting every N hits after the first, per-symbol cooldowns, rich embeds, and
auto-reconnect. Everything here is meant to be obvious, not production-hardened.

Run:  DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." \\
      .venv/bin/python -m notify_discord.notify_simple
"""
import asyncio
import json
import os
import ssl
import urllib.request

import websockets

# --- Config (three env vars, that's it) ------------------------------------- #
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
WS_URL = os.environ.get("HLT_ALGO_WS", "ws://127.0.0.1:7412").strip()
MILESTONE = int(os.environ.get("HLT_MILESTONE", "5"))  # alert on the Nth new high/low

# Discord needs verified TLS. The stock python.org build often has no CA bundle,
# so we hand urllib the certifi bundle if it's installed (it is, in the venv).
try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL = ssl.create_default_context()


def post_to_discord(text: str) -> None:
    """POST one plain-text message to the webhook."""
    data = json.dumps({"content": text}).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK,
        data=data,
        headers={
            "Content-Type": "application/json",
            # Discord is behind Cloudflare, which blocks the default
            # "Python-urllib/x.y" User-Agent with a 403. Send a real one.
            "User-Agent": "HLT-Notifier-Simple/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, context=_SSL, timeout=10) as resp:
        resp.read()


async def main() -> None:
    if not WEBHOOK:
        raise SystemExit("Set DISCORD_WEBHOOK_URL first.")

    # Remember which (symbol, side) pairs we've already alerted, so each hot name
    # pings exactly once instead of on every new high after the milestone.
    alerted: set[tuple[str, str]] = set()

    print(f"[simple] connecting to {WS_URL} (alert on hit #{MILESTONE}) …", flush=True)
    async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=120) as ws:
        async for raw in ws:
            ev = json.loads(raw)
            if ev.get("type") != "TAPE_EVENT":
                continue  # ignore price_update / market_summary / etc.

            symbol = ev.get("symbol")
            event = ev.get("event", "")

            # A frame can be a new high, a new low, or both at once.
            for side, count_key in (("high", "high_count"), ("low", "low_count")):
                if side not in event:  # "new_high", "new_low", "new_high_and_low"
                    continue
                count = ev.get(count_key)
                key = (symbol, side)
                if count is None or count < MILESTONE or key in alerted:
                    continue

                alerted.add(key)
                arrow = "🔼" if side == "high" else "🔽"
                price = ev.get("last_price")
                price_str = f" @ ${price:,.2f}" if price is not None else ""
                post_to_discord(f"{arrow} {symbol} · {count} new {side}s{price_str}")
                print(f"[simple] {symbol} {side} #{count} -> Discord", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[simple] stopped", flush=True)
