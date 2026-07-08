# algos

Python paper-trading strategies that consume the [HighLowTicker](https://highlowtick.com) algo tape over WebSocket.

This repo ships:

- **`core/`** — shared runtime (websocket feed loop, timestamps, session reporting)
- **`algo_spy/`** — breadth-first SPY strategy (`spy_breadth_ema_v2`)

## Prerequisites

- Python 3.9+
- **HighLowTicker** (or compatible build) running with the algo server on `ws://127.0.0.1:7412`
- For live warmup: network access to Yahoo Finance (`yfinance`)

## Quick start

```bash
git clone https://github.com/jach8/algos.git
cd algos

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp algo_spy/.env.example algo_spy/.env
# edit algo_spy/.env if needed

python -m algo_spy.main
```

See **[howtorun.md](howtorun.md)** for the full workflow (replay, charts, tests).

## Layout

```
algos/
├── core/           # Shared websocket client + reporting
├── algo_spy/       # SPY breadth + optional EMA strategy
├── requirements.txt
├── howtorun.md
└── README.md
```

## algo_spy (summary)

Market **breadth score** from HighLowTicker `TAPE_EVENT` rate bars drives entries and most exits. Optional EMA(3/6/10) on 5-minute closes (`ema_mode`: `breadth` | `full` | `off`).

| Item | Value |
|------|-------|
| Algo ID | `spy_breadth_ema_v2` |
| Default symbol | `SPY` |
| Entry timing | 5m bar close, debounced breadth threshold |
| Paper fills | Commission + slippage + spread model in `execution.py` |

Strategy details: [algo_spy/readme.md](algo_spy/readme.md)

## Creating another algo

1. Add `your_algo/` with `main.py`, `strategy.py`, `report.py`
2. Import shared runtime from `core` (do not duplicate websocket logic):

   ```python
   from core.feed import run_feed_loop
   from core.time_utils import event_est, now_est
   from core.reporting import print_summary
   ```

3. Implement `Strategy` with `symbol`, `account`, `last_price`, and emit `ALGO_SIGNAL` / `ALGO_ORDER` / `ALGO_FILL` events
4. Wire `handle_tape_event` + `run_feed_loop` like `algo_spy/main.py`

## License

MIT — see [LICENSE](LICENSE).
