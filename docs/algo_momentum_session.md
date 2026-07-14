"""algo_momentum — realtime multi-symbol momentum paper trader for HighLowTicker.

Created for live session paper trading. Trades names printing new highs with
volume spikes, gated by market breadth. Uses the same commission / slippage /
spread fill model as algo_spy so PnL is comparable across strategies.

## Run tomorrow

```bash
# Terminal 1 — HighLowTicker algo server
cargo run --release -- --algo-mode   # ws://127.0.0.1:7412

# Terminal 2 — breadth SPY (existing)
python -m algo_spy.main

# Terminal 3 — realtime momentum leaders (new)
cp algo_momentum/.env.example algo_momentum/.env
python -m algo_momentum.main
```

See `algo_momentum/readme.md` for parameters and fill realism details.
"""
