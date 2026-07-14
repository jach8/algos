# How to run algos

Paper-trading clients for HighLowTicker. Connect to the algo WebSocket (default `ws://127.0.0.1:7412`).

## 1. One-time setup

```bash
git clone https://github.com/jach8/algos.git
cd algos

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp algo_spy/.env.example algo_spy/.env
cp algo_momentum/.env.example algo_momentum/.env
```

Edit `.env` files for trail params, sizing, entry cutoff, etc.

## 2. Start HighLowTicker (tape producer)

You need a HighLowTicker build streaming the algo tape. Typical local setup:

```bash
# In your HighLowTicker / highlowticker-rs-2 tree:
cargo run --release -- --algo-mode
```

The algo server listens on **`ws://127.0.0.1:7412`**. Without it, clients will reconnect every 5s and wait.

## 3. Live paper sessions

From the **repo root** (`algos/`):

### Breadth SPY (`algo_spy`)

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

Logs: `algo_spy/trades.jsonl`, `algo_spy/breadth.jsonl`, optional `algo_spy/sessions/*_tape.jsonl`.

### Momentum leaders (`algo_momentum`)

Realtime multi-symbol paper trader — enters leaders printing new highs + volume spikes when market breadth confirms. Same fill realism as `algo_spy` (commission + slippage + spread).

```bash
python -m algo_momentum.main
python -m algo_momentum.main --max-positions 3 --cash-fraction 0.25
python -m algo_momentum.main --trail-arm 0.005 --trail-pct 0.002
```

Logs: `algo_momentum/trades.jsonl`.

**Ctrl+C** on either client flattens open positions (with fill costs) and prints a session summary.

## 4. Replay a recorded session (algo_spy)

```bash
python -m algo_spy.replay_session
# or pick a tape:
python -m algo_spy.replay_session --tape algo_spy/sessions/20260630_tape.jsonl
python -m algo_spy.replay_session --trail-arm 0.0008 --quiet
```

Uses the same `handle_tape_event` path as live — good for parameter sweeps without market hours.

## 5. Session chart (algo_spy)

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
python -m pytest algo_spy/tests/ algo_momentum/tests/ core/tests/ -q
```

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `ModuleNotFoundError: core` | Run commands from repo root with venv active |
| Reconnect loop, no ticks | Algo server not running on `:7412` |
| Yahoo warmup skipped | Offline or `yfinance` error — use `--no-warmup` or fix network |
| No entries near close | Entry cutoff env (`ALGO_SPY_ENTRY_CUTOFF_ET` / `ALGO_MOM_ENTRY_CUTOFF_ET`, default 15:30 ET) |
| `algo_momentum` never enters | Need bullish breadth + persistent hot leader + `volume_spike` on `new_high` |
