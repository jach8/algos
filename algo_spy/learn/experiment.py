"""Walk-forward, prequential evaluation of the online entry filter on one session."""
from __future__ import annotations

import argparse
from pathlib import Path

from core.time_utils import parse_iso

from ..bars import BAR_5M_SEC, BarAggregator
from ..env_config import load_dotenv_file
from ..tape_recorder import load_tape_log, row_to_tape_event, tape_dir
from .costs import round_trip_cost_per_share
from .dataset import build_candidates_for_tape
from .features import FEATURE_NAMES
from .online_lr import OnlineLogisticRegression
from .oracle import buy_and_hold_per_share, oracle_pnl_per_share


def _auc(pairs: list[tuple[float, int]]) -> float:
    pos = [p for p, y in pairs if y == 1]
    neg = [p for p, y in pairs if y == 0]
    if not pos or not neg:
        return float("nan")
    wins = sum((a > b) + 0.5 * (a == b) for a in pos for b in neg)
    return wins / (len(pos) * len(neg))


def _session_closes(path: Path) -> list[float]:
    bars = BarAggregator(period_sec=BAR_5M_SEC)
    closes: list[float] = []
    for row in load_tape_log(path):
        ev = row_to_tape_event(row)
        if ev.get("symbol") != "SPY":
            continue
        ts = ev.get("ts") or ""
        px = ev.get("last_price")
        if not ts or px is None:
            continue
        bars.on_tick(parse_iso(ts), float(px))
        for bar in bars.pop_closed():
            closes.append(bar.close)
    return closes


def run(target: Path, history: list[Path], *, lr: float, l2: float) -> None:
    model = OnlineLogisticRegression(n_features=len(FEATURE_NAMES), lr=lr, l2=l2)
    n_train = 0
    for path in history:
        for c in build_candidates_for_tape(path):
            if c.label is None:
                continue
            model.update(c.features, c.label)
            n_train += 1

    cands = [c for c in build_candidates_for_tape(target) if c.label is not None]
    cands.sort(key=lambda c: c.ts_sec)
    auc_pairs: list[tuple[float, int]] = []
    baseline_pnl = filtered_pnl = 0.0
    taken = wins = 0
    for c in cands:
        assert c.label is not None and c.net_return is not None
        p = model.predict_proba(c.features)
        auc_pairs.append((p, c.label))
        baseline_pnl += c.net_return
        if p >= 0.5:
            filtered_pnl += c.net_return
            taken += 1
            wins += c.label
        model.update(c.features, c.label)

    closes = _session_closes(target)
    avg_close = sum(closes) / len(closes) if closes else 0.0
    cost = round_trip_cost_per_share(avg_close)
    oracle = oracle_pnl_per_share(closes, cost_per_share=cost)
    bh = buy_and_hold_per_share(closes)

    assert baseline_pnl <= oracle + 1e-6, "LEAK: baseline beat the oracle ceiling"
    assert filtered_pnl <= oracle + 1e-6, "LEAK: filtered beat the oracle ceiling"

    base_rate = sum(c.label for c in cands) / len(cands) if cands else float("nan")
    print(f"train candidates (prior sessions): {n_train}")
    print(
        f"target session: {target.stem.replace('_tape', '')}  candidates={len(cands)}  "
        f"base_win_rate={base_rate:.3f}"
    )
    print(f"AUC (prequential, today): {_auc(auc_pairs):.3f}")
    print(f"baseline take-all  P&L/share: {baseline_pnl:+.3f}  (n={len(cands)})")
    if taken:
        print(
            f"filtered  p>=0.5   P&L/share: {filtered_pnl:+.3f}  (n={taken}, "
            f"win_rate={wins / taken:.3f})"
        )
    else:
        print("filtered  p>=0.5: no trades taken")
    print(
        f"oracle ceiling P&L/share: {oracle:+.3f}   buy&hold: {bh:+.3f}   "
        f"round_trip_cost/share≈{cost:.3f}"
    )
    if oracle > 0:
        print(
            f"capture — baseline: {baseline_pnl / oracle:5.1%}   "
            f"filtered: {filtered_pnl / oracle:5.1%}"
        )


def main() -> None:
    load_dotenv_file()
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--target", type=Path, default=None, help="target *_tape.jsonl (default: latest)"
    )
    ap.add_argument("--lr", type=float, default=0.05)
    ap.add_argument("--l2", type=float, default=1e-4)
    args = ap.parse_args()
    tapes = sorted(tape_dir().glob("*_tape.jsonl"), key=lambda p: p.stem)
    if not tapes:
        raise SystemExit("no tapes found")
    target = args.target or tapes[-1]
    history = [p for p in tapes if p.stem < target.stem]
    run(target, history, lr=args.lr, l2=args.l2)


if __name__ == "__main__":
    main()
