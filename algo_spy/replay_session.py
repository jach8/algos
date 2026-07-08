"""Replay a recorded tape session through algo_spy (or another strategy hook)."""
from __future__ import annotations

import argparse
from pathlib import Path

from .env_config import load_dotenv_file
from .get_previous_data import warmup_strategy_from_yahoo
from .main import handle_tape_event
from .report import print_summary
from .strategy import ALGO_ID, Strategy
from .tape_recorder import latest_tape_log, load_tape_log, row_to_tape_event


def replay_tape(
    strat: Strategy,
    rows: list[dict],
    *,
    quiet: bool = False,
) -> int:
    """Feed recorded rows in time order. Returns fill count."""
    fills = 0
    for row in rows:
        ev = row_to_tape_event(row)
        ts = ev.get("ts") or ""
        if not ts:
            continue
        for out in handle_tape_event(strat, ev, record_tape=False, log_breadth=False):
            if out.get("type") == "ALGO_FILL":
                fills += 1
                if not quiet:
                    side = out.get("side", "")
                    px = out.get("fill_price") or out.get("price")
                    print(f"[replay] FILL {side} @ {px} ts={ts}")
    return fills


def main() -> None:
    load_dotenv_file()
    parser = argparse.ArgumentParser(description="Replay recorded algo_spy tape session")
    parser.add_argument(
        "--tape",
        type=Path,
        default=None,
        help="path to *_tape.jsonl (default: latest in sessions/)",
    )
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--warmup-bars", type=int, default=24)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--trail-arm", type=float, default=None)
    parser.add_argument("--trail-pct", type=float, default=None)
    parser.add_argument("--trail-min-hold", type=float, default=None)
    args = parser.parse_args()

    tape_path = args.tape or latest_tape_log()
    if tape_path is None or not tape_path.is_file():
        raise SystemExit("no tape file — run a live session first or pass --tape")

    rows = load_tape_log(tape_path)
    if not rows:
        raise SystemExit(f"empty tape: {tape_path}")

    strat_kw: dict = {"symbol": args.symbol}
    if args.trail_arm is not None:
        strat_kw["trail_activation_pct"] = args.trail_arm
    if args.trail_pct is not None:
        strat_kw["trail_pct"] = args.trail_pct
    if args.trail_min_hold is not None:
        strat_kw["trail_min_hold_sec"] = args.trail_min_hold

    strat = Strategy(**strat_kw)
    if not args.no_warmup:
        try:
            n = warmup_strategy_from_yahoo(strat, args.symbol, max_bars=args.warmup_bars)
            print(f"Yahoo warmup: {n} bars, EMA {'ready' if strat.ema.ready() else 'warming'}")
        except Exception as exc:
            print(f"Yahoo warmup skipped: {exc}")

    print(f"Replaying {len(rows)} events from {tape_path}")
    n_fills = replay_tape(strat, rows, quiet=args.quiet)
    print(f"Replay fills during run: {n_fills}")
    print_summary(strat, ALGO_ID)


if __name__ == "__main__":
    main()
