# algo_momentum — realtime multi-symbol momentum

**Algo ID:** `momentum_leaders_v1`

Trades individual stocks printing **new highs with volume spikes** on the HighLowTicker
tape, gated by market breadth. Paper fills use the same realism model as `algo_spy`
(commission + slippage + NBBO/spread).

## Idea

1. **Market filter** — only open new longs when `buy_pct_5m ≥ 65` and
   `market_high_rate_5m / market_low_rate_5m ≥ 2`.
2. **Leader persistence** — symbol needs ≥10 cumulative highs and ≥3 highs in the
   last 5 minutes, with **no** overlapping lows in that window (chop filter).
3. **Entry** — `new_high` + `volume_spike=True` on a qualified name (realtime).
4. **Exits** — stacking recent lows, 4% stop, trailing stop, breadth kill-switch
   (`buy_pct_5m < 50`), session-end flatten.
5. **Sizing** — up to `MAX_POSITIONS` concurrent longs; each uses
   `CASH_FRACTION` of free cash (whole shares, commission-aware).

## Run

```bash
# From repo root, HighLowTicker algo server on :7412
cp algo_momentum/.env.example algo_momentum/.env
python -m algo_momentum.main
```

Flags:

```bash
python -m algo_momentum.main --max-positions 3 --cash-fraction 0.25
python -m algo_momentum.main --trail-arm 0.005 --trail-pct 0.002
```

**Ctrl+C** flattens open positions with fill costs and writes `algo_momentum/trades.jsonl`.

## Execution realism

Same cost stack as `algo_spy/execution.py`:

| Component | Value |
|-----------|-------|
| Commission | $0.65 / share each leg |
| Slippage | uniform adverse 0–3 bps |
| Spread | pay ask / receive bid when NBBO on tape; else last ± $0.01 (`DEFAULT_STOCK_SPREAD=$0.02`) |

Trade PnL: `(exit − entry) * qty − (entry_comm + exit_comm)`.

## Parameters (env)

| Key | Default |
|-----|---------|
| `ALGO_MOM_MAX_POSITIONS` | 5 |
| `ALGO_MOM_CASH_FRACTION` | 0.20 |
| `ALGO_MOM_TRAIL_ARM` | 0.004 |
| `ALGO_MOM_TRAIL_PCT` | 0.002 |
| `ALGO_MOM_TRAIL_MIN_HOLD_SEC` | 60 |
| `ALGO_MOM_ENTRY_CUTOFF_ET` | 15:30 |

## Tests

```bash
python -m pytest algo_momentum/tests/ -q
```
