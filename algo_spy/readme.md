# algo_spy v2 — breadth-first (EMA optional)

**Algo ID:** `spy_breadth_ema_v2`

Market rate bars (HighLowTicker `TAPE_EVENT`) drive entries and most exits. EMA(3/6/10) on **5-minute closes** is optional via `ema_mode`.

Spec: `docs/algo_spy_v2_phase0_spec.md`

## Idea

- **Breadth score** — weighted sum of 30s/1m/5m/20m high−low rate bias (weights 4/3/2/1). Sign and magnitude lead SPY on the chart.
- **Default (`ema_mode=breadth`)** — enter when total score passes threshold **and** short/medium subscores align (`short≥0 & medium>0` long; `short≤0 & medium<0` short). Block longs in **divergence**. EMA3 hard veto only (no compression/ignition filter). No `ema_structural` exit.
- **`ema_mode=full`** — legacy v2: EMA confirm/reload on entry + EMA structural exit on 5m close.
- **`ema_mode=off`** — pure breadth; no EMA veto at all.
- **Entries** — only on **5m bar close**, after breadth has been ≥ `T_ENTRY` (or ≤ −`T_ENTRY`) for **2 consecutive tape events**.
- **Exits** — stop on tick; trail (env-tuned); breadth medium roll / short warning on tape; 30‑min time stop if flat P&L.
- **Divergence** — short-TF bearish while SPY structure still up → block new longs, hold longs unless medium rolls.

## Parameters

| Module | Constant | Value |
|--------|----------|-------|
| `throughput.py` | `T_ENTRY` | ±10 |
| | `T_WARN` | ±4 (short_breadth) |
| | `T_FLAT` | ±3 |
| | `ENTRY_DEBOUNCE` | 2 tape events |
| | `T_EXIT_MEDIUM` | ±50 (medium roll exit margin) |
| | `EXIT_MEDIUM_CONFIRM` | 5 tape events |
| `ema_filter.py` | `T_EMA_SOFT` | ±1 (full mode only) |
| `strategy.py` | `MIN_HOLD_SEC` | 300 (5m) |
| | `REENTRY_COOLDOWN_SEC` | 300 |
| | `STOP_PCT` | 4% |
| | `TRAIL_ACTIVATION_PCT` | **0.20%** arm (≥ commission breakeven) |
| | `TRAIL_PCT` | **0.10%** pullback from peak |
| | `TRAIL_MIN_HOLD_SEC` | **60s** (trail only; breadth exits still 5m) |
| | `TIME_STOP_SEC` | 30 min (skipped while breadth still aligned) |
| | `ENTRY_CUTOFF_ET` | **15:30** — no new entries after (env `ALGO_SPY_ENTRY_CUTOFF_ET`) |
| | `ALGO_SPY_EMA_MODE` | `breadth` (default), `full`, or `off` |

**Settings** — copy `.env.example` to `.env` in this folder:

```bash
# algo_spy/.env
ALGO_SPY_EMA_MODE=breadth
ALGO_SPY_TRAIL_ARM=0.002
ALGO_SPY_TRAIL_PCT=0.001
ALGO_SPY_TRAIL_MIN_HOLD_SEC=60
ALGO_SPY_ENTRY_CUTOFF_ET=15:30
```

Loaded automatically when you run `python -m algo_spy.main`. CLI flags override `.env` (`--ema-mode`, `--trail-arm`, etc.).

## Tape replay / backtest

Every live session saves **full throughput + SPY ticks** for later replay:

| File | Contents |
|------|----------|
| `sessions/YYYYMMDD_tape.jsonl` | Each TAPE_EVENT: 8 market rate fields, SPY price, event kind |
| `sessions/YYYYMMDD_meta.json` | Session start/end, event count |

Recording is on by default (`ALGO_SPY_RECORD_TAPE=1`). Set `0` in `.env` to disable.

**Replay** through the current strategy (try different trail params without live tape):

```bash
# From repo root, with venv active
python -m algo_spy.replay_session
# or: python -m algo_spy.replay_session --tape algo_spy/sessions/20260529_tape.jsonl
python -m algo_spy.replay_session --trail-arm 0.0008 --quiet
```

Use the same `handle_tape_event` path as live — swap `Strategy` later for variant tests.

**Tick exits:** stop (immediate); trailing stop after **1m**; time stop after 5m min hold + 30m flat.

## Run (paper)

```bash
# HighLowTicker tape on ws://127.0.0.1:7412 — from repo root
python -m algo_spy.main
```

Fill logs include breadth meta: `score`, `short`, `med`, `div=`, EMA scores.

## Session chart

After a paper session, render SPY + 5m EMAs, breadth subscores, and session P&L:

```bash
python -m algo_spy.replay_chart
# → algo_spy_session_YYYYMMDD.png  (price, breadth, cumulative P&L)
```

Reads `trades.jsonl` and `breadth.jsonl` (logged automatically during paper sessions).

## Tests

```bash
python -m pytest algo_spy/tests/ -q
```

## Execution realism (`execution.py`)

- **Commission**: $0.65 / share on every fill (entry + exit).
- **Slippage**: random adverse 0–3 bps on each fill.
- **Spread**: pay ask / receive bid when NBBO on tape; else last ± $0.005 with $0.01 default spread.
