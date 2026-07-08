"""Grid-search exit params on recorded tapes (accept overfit risk — sanity check only).

Focus on trail knobs first (decomposition showed exits as the main leak).
"""
from __future__ import annotations

import argparse
import itertools
import random
from dataclasses import dataclass
from pathlib import Path

from .env_config import load_dotenv_file
from .get_previous_data import warmup_strategy_from_yahoo
from .report import flatten_open_position
from .replay_session import replay_tape
from .strategy import Strategy
from .tape_recorder import latest_tape_log, load_tape_log, tape_dir


@dataclass(frozen=True)
class SweepResult:
    trail_arm: float
    trail_pct: float
    trail_min_hold: float
    session: str
    n_trades: int
    pnl_per_share: float
    total_pnl: float


def _replay_pnl(
    tape_path: Path,
    *,
    symbol: str,
    trail_arm: float,
    trail_pct: float,
    trail_min_hold: float,
    warmup_bars: int,
    seed: int,
) -> tuple[int, float, float]:
    random.seed(seed)
    rows = load_tape_log(tape_path)
    if not rows:
        return 0, 0.0, 0.0
    strat = Strategy(
        symbol=symbol,
        trail_activation_pct=trail_arm,
        trail_pct=trail_pct,
        trail_min_hold_sec=trail_min_hold,
    )
    try:
        warmup_strategy_from_yahoo(strat, symbol, max_bars=warmup_bars)
    except Exception:
        pass
    replay_tape(strat, rows, quiet=True)
    flatten_open_position(strat, reason="session_end_flatten")
    n = len(strat.account.closed)
    if n == 0:
        return 0, 0.0, 0.0
    per_sh = sum(t.pnl / abs(t.qty) for t in strat.account.closed)
    total = sum(t.pnl for t in strat.account.closed)
    return n, per_sh, total


def run_sweep(
    tapes: list[Path],
    *,
    trail_arms: list[float],
    trail_pcts: list[float],
    trail_min_holds: list[float],
    symbol: str = "SPY",
    warmup_bars: int = 120,
) -> list[SweepResult]:
    out: list[SweepResult] = []
    for tape_path in tapes:
        session = tape_path.stem.replace("_tape", "")
        for arm, pct, hold in itertools.product(trail_arms, trail_pcts, trail_min_holds):
            seed = hash((session, arm, pct, hold)) & 0xFFFFFFFF
            n, per_sh, total = _replay_pnl(
                tape_path,
                symbol=symbol,
                trail_arm=arm,
                trail_pct=pct,
                trail_min_hold=hold,
                warmup_bars=warmup_bars,
                seed=seed,
            )
            out.append(
                SweepResult(arm, pct, hold, session, n, per_sh, total)
            )
    return out


def _aggregate_by_params(results: list[SweepResult]) -> list[tuple[SweepResult, float]]:
    """Sum pnl_per_share across sessions for each param triple."""
    buckets: dict[tuple[float, float, float], list[SweepResult]] = {}
    for r in results:
        key = (r.trail_arm, r.trail_pct, r.trail_min_hold)
        buckets.setdefault(key, []).append(r)
    agg: list[tuple[SweepResult, float]] = []
    for key, rows in buckets.items():
        total_per_sh = sum(r.pnl_per_share for r in rows)
        rep = rows[0]
        agg.append((rep, total_per_sh))
    agg.sort(key=lambda x: x[1], reverse=True)
    return agg


def main() -> None:
    load_dotenv_file()
    ap = argparse.ArgumentParser(description="Exit param grid search on tape replay")
    ap.add_argument("--tape", type=Path, default=None, help="one tape (default: all in sessions/)")
    ap.add_argument("--all-sessions", action="store_true", help="sweep every *_tape.jsonl")
    ap.add_argument("--top", type=int, default=15, help="show top N configs")
    ap.add_argument(
        "--trail-arms",
        default="0.0015,0.002,0.0025,0.003,0.004",
        help="comma-separated decimal pct",
    )
    ap.add_argument(
        "--trail-pcts",
        default="0.0008,0.001,0.0012,0.0015,0.002",
        help="comma-separated decimal pct",
    )
    ap.add_argument(
        "--trail-min-holds",
        default="60,120,180,300",
        help="comma-separated seconds",
    )
    args = ap.parse_args()

    if args.all_sessions:
        tapes = sorted(tape_dir().glob("*_tape.jsonl"), key=lambda p: p.stem)
    elif args.tape is not None:
        tapes = [args.tape]
    else:
        latest = latest_tape_log()
        if latest is None:
            raise SystemExit("no tape found")
        tapes = [latest]

    arms = [float(x) for x in args.trail_arms.split(",")]
    pcts = [float(x) for x in args.trail_pcts.split(",")]
    holds = [float(x) for x in args.trail_min_holds.split(",")]

    print(f"Sweeping {len(tapes)} tape(s), {len(arms)*len(pcts)*len(holds)} configs each")
    print("(Overfit risk: high if you pick the winner on the same tape you tuned on.)")
    print()

    results = run_sweep(tapes, trail_arms=arms, trail_pcts=pcts, trail_min_holds=holds)
    if len(tapes) == 1:
        ranked = sorted(results, key=lambda r: r.pnl_per_share, reverse=True)
        print(
            f"{'arm':>7} {'pct':>7} {'hold':>6} {'trades':>6} {'pnl/sh':>10}  session"
        )
        print("-" * 52)
        for r in ranked[: args.top]:
            print(
                f"{r.trail_arm:7.4f} {r.trail_pct:7.4f} {r.trail_min_hold:6.0f} "
                f"{r.n_trades:6d} {r.pnl_per_share:+10.2f}  {r.session}"
            )
        best = ranked[0]
        print()
        print(
            "Best on this tape: "
            f"ALGO_SPY_TRAIL_ARM={best.trail_arm} "
            f"ALGO_SPY_TRAIL_PCT={best.trail_pct} "
            f"ALGO_SPY_TRAIL_MIN_HOLD_SEC={int(best.trail_min_hold)} "
            f"(pnl/sh={best.pnl_per_share:+.2f}, trades={best.n_trades})"
        )
    else:
        agg = _aggregate_by_params(results)
        print(f"{'arm':>7} {'pct':>7} {'hold':>6} {'Σ pnl/sh':>10}  (summed across sessions)")
        print("-" * 44)
        for rep, total in agg[: args.top]:
            print(
                f"{rep.trail_arm:7.4f} {rep.trail_pct:7.4f} {rep.trail_min_hold:6.0f} "
                f"{total:+10.2f}"
            )
        rep, total = agg[0]
        print()
        print(
            "Best aggregate (do NOT trust without hold-out day): "
            f"ALGO_SPY_TRAIL_ARM={rep.trail_arm} "
            f"ALGO_SPY_TRAIL_PCT={rep.trail_pct} "
            f"ALGO_SPY_TRAIL_MIN_HOLD_SEC={int(rep.trail_min_hold)} "
            f"(Σ pnl/sh={total:+.2f})"
        )


if __name__ == "__main__":
    main()
