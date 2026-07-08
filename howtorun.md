# How to run algo_spy

Paper-trading client for SPY. Connects to the HighLowTicker algo WebSocket (default `ws://127.0.0.1:7412`).

## 1. One-time setup

```bash
git clone https://github.com/jach8/algos.git
cd algos

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp algo_spy/.env.example algo_spy/.env
```

Edit `algo_spy/.env` for trail params, EMA mode, entry cutoff, tape recording, etc.

## 2. Start HighLowTicker (tape producer)

You need a HighLowTicker build streaming the algo tape. Typical local setup:

```bash
# In your HighLowTicker / highlowticker-rs-2 tree:
cargo run --release -- --algo-mode
```

The algo server listens on **`ws://127.0.0.1:7412`**. Without it, `algo_spy` will reconnect every 5s and wait.

## 3. Live paper session

From the **repo root** (`algos/`):

```bash
source .venv/bin/activate
python -m algo_spy.main
```

Common flags:

```bash
python -m algo_spy.main --symbol SPY --url ws://127.0.0.1:7412
python -m algo_spy.main --no-warmup          # skip Yahoo 5m EMA seed
python -m algo_spy.main --ema-mode off       # pure breadth, no EMA veto
python -m algo_spy.main --trail-arm 0.002 --trail-pct 0.001
```

**Ctrl+C** flattens any open position and prints a session summary. Logs:

| File | Purpose |
|------|---------|
| `algo_spy/trades.jsonl` | Fills + closed trades |
| `algo_spy/breadth.jsonl` | Breadth subscores over time |
| `algo_spy/sessions/YYYYMMDD_tape.jsonl` | Full tape replay log (if `ALGO_SPY_RECORD_TAPE=1`) |

## 4. Replay a recorded session (no live feed)

```bash
python -m algo_spy.replay_session
# or pick a tape:
python -m algo_spy.replay_session --tape algo_spy/sessions/20260630_tape.jsonl
python -m algo_spy.replay_session --trail-arm 0.0008 --quiet
```

Uses the same `handle_tape_event` path as live — good for parameter sweeps without market hours.

## 5. Session chart

After a paper session:

```bash
python -m algo_spy.replay_chart
# or explicit paths:
python -m algo_spy.replay_chart \
  --trades algo_spy/trades.jsonl \
  --breadth algo_spy/breadth.jsonl
```

Writes `algo_spy_session_YYYYMMDD.png` in the current directory (SPY price, breadth subscores, cumulative P&L).

## 6. Tests

```bash
python -m pytest algo_spy/tests/ core/tests/ -q
```

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `ModuleNotFoundError: core` | Run commands from repo root with venv active |
| Reconnect loop, no ticks | Algo server not running on `:7412` |
| Yahoo warmup skipped | Offline or `yfinance` error — use `--no-warmup` or fix network |
| No entries near close | `ALGO_SPY_ENTRY_CUTOFF_ET` (default 15:30 ET) |
