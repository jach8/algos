# Discord Momentum Notifier

Turns the HighLowTicker algo feed into **pushed alerts** — instead of watching the
tape all day, get a Discord ping when a symbol *repeatedly* prints new session
highs/lows (a momentum signal).

It keys off the feed's own `high_count` / `low_count`, so you are not flooded with
a message on every single new high. You get one alert when a name reaches a
milestone (default: its 5th new high), then again every few hits after.

## Requirements

- HighLowTicker running with the **algo feed enabled** (Settings → algo feed, or
  `[algo] enabled = true` in `config.toml`). The feed listens on `ws://127.0.0.1:7412`.
- A Discord **webhook URL** (Server Settings → Integrations → Webhooks → New Webhook).

## Two versions

- **`notify_simple.py`** — the ~95-line, read-it-in-one-sitting version. Connect →
  watch `high_count`/`low_count` → ping once when a symbol hits its 5th new high/low.
  Start here to understand how it works.
- **`notify.py`** — the full version. Same core idea plus watchlist/side filters,
  re-alerting every N hits, per-symbol cooldowns, rich embeds, and auto-reconnect.

## Run

```bash
# simple version
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/…" \
  python -m notify_discord.notify_simple

# full version
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/…" \
  python -m notify_discord.notify
```

(From the `algos/` directory, or use `algos/.venv/bin/python`.)

## Configuration (environment variables)

| Var | Default | Meaning |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | (required) | Where alerts are posted |
| `HLT_ALGO_WS` | `ws://127.0.0.1:7412` | Algo feed WebSocket URL |
| `HLT_WATCH` | (empty = all) | Comma-separated symbols to limit alerts to, e.g. `NVDA,AAPL,SPY` |
| `HLT_SIDES` | `both` | `highs`, `lows`, or `both` |
| `HLT_MILESTONE` | `5` | First alert when a symbol reaches this new-high/low count |
| `HLT_STEP` | `5` | Alert again every N counts after the milestone (10th, 15th, …) |
| `HLT_COOLDOWN_SECS` | `60` | Minimum gap between alerts for the same symbol |

## How it works

```
algo feed (ws)  ──TAPE_EVENT──▶  parse_readings  ──▶  watchlist/side filter
                                                          │
                                                          ▼
                                              MilestoneGate.should_fire
                                                          │ (true)
                                                          ▼
                                              format_alert ──▶ Discord webhook
```

All decision logic (`parse_readings`, `MilestoneGate`, `format_alert`) is pure and
unit-tested in `tests/`; the async loop in `notify.py` is a thin I/O shell. It is a
single self-contained file — copy `notify.py`, set the env vars, run.

## Tests

```bash
cd algos && .venv/bin/python -m pytest notify_discord/tests -q
```
