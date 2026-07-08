"""Oracle-imitation experiment — can causal features predict the optimal action?

This is the 'dead horse' test: if features cannot predict the oracle's per-bar
{flat, long, short} labels prequentially, a pure-ML strategy has no foundation.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from core.time_utils import parse_iso

from ..bars import BAR_5M_SEC, BarAggregator, EmaStack
from ..env_config import load_dotenv_file
from ..tape_recorder import load_tape_log, row_to_tape_event, tape_dir
from ..throughput import MarketThroughput
from .costs import round_trip_cost_per_share
from .decomposition import session_bar_closes
from .features import ORACLE_FEATURE_NAMES, bar_features
from .online_lr import OnlineLogisticRegression
from .oracle import FLAT, LONG, SHORT
from .oracle_trace import oracle_path_states

STATE_NAMES = ("flat", "long", "short")
N_CLASSES = 3


@dataclass
class BarSample:
    session: str
    bar_idx: int
    features: list[float]
    label: int  # FLAT=0, LONG=1, SHORT=2


def _oracle_labels_by_bar(closes: list[float], cost: float) -> dict[int, int]:
    path, _ = oracle_path_states(closes, cost_per_share=cost)
    return {bar_i: state for bar_i, state in path}


def build_bar_samples(path: Path) -> list[BarSample]:
    session = path.stem.replace("_tape", "")
    bars = session_bar_closes(path)
    closes = [b.close for b in bars]
    if len(closes) < 2:
        return []
    cost = round_trip_cost_per_share(sum(closes) / len(closes))
    labels = _oracle_labels_by_bar(closes, cost)

    throughput = MarketThroughput()
    ema = EmaStack()
    agg = BarAggregator(period_sec=BAR_5M_SEC)
    samples: list[BarSample] = []
    bar_idx = 0

    for row in load_tape_log(path):
        ev = row_to_tape_event(row)
        ts = ev.get("ts") or ""
        if not ts:
            continue
        ts_sec = parse_iso(ts)
        throughput.update_from_tape_event(ev)
        if ev.get("symbol") != "SPY":
            continue
        px = ev.get("last_price")
        if px is None:
            continue
        agg.on_tick(ts_sec, float(px))
        for bar in agg.pop_closed():
            ema.update(bar.close)
            if not ema.ready():
                bar_idx += 1
                continue
            if bar_idx not in labels:
                bar_idx += 1
                continue
            ema1, _, ema3 = ema.levels()
            assert ema1 is not None and ema3 is not None
            bar_ts = bar.bucket_epoch * BAR_5M_SEC + BAR_5M_SEC
            snap = throughput.breadth_snapshot()
            samples.append(
                BarSample(
                    session=session,
                    bar_idx=bar_idx,
                    features=bar_features(
                        breadth=snap,
                        close=bar.close,
                        ema1=ema1,
                        ema3=ema3,
                        ts_sec=bar_ts,
                    ),
                    label=labels[bar_idx],
                )
            )
            bar_idx += 1
    return samples


class OnlineOvRClassifier:
    """One-vs-rest online logistic classifiers for {flat, long, short}."""

    def __init__(self, n_features: int, *, lr: float = 0.05, l2: float = 1e-4) -> None:
        self.models = [
            OnlineLogisticRegression(n_features, lr=lr, l2=l2) for _ in range(N_CLASSES)
        ]

    def predict(self, x: list[float]) -> int:
        probas = [m.predict_proba(x) for m in self.models]
        return max(range(N_CLASSES), key=lambda c: probas[c])

    def update(self, x: list[float], label: int) -> None:
        for c, model in enumerate(self.models):
            model.update(x, 1 if c == label else 0)


def _accuracy(pairs: list[tuple[int, int]]) -> float:
    if not pairs:
        return float("nan")
    return sum(p == y for p, y in pairs) / len(pairs)


def _flat_baseline_accuracy(samples: list[BarSample]) -> float:
    if not samples:
        return float("nan")
    flat_count = sum(1 for s in samples if s.label == FLAT)
    return flat_count / len(samples)


def _majority_baseline_accuracy(samples: list[BarSample]) -> float:
    if not samples:
        return float("nan")
    counts = [0, 0, 0]
    for s in samples:
        counts[s.label] += 1
    return max(counts) / len(samples)


@dataclass
class SessionImitationResult:
    session: str
    n_bars: int
    oracle_pnl: float
    model_acc: float
    flat_baseline: float
    majority_baseline: float
    lift_vs_flat: float
    lift_vs_majority: float


def evaluate_session(
    target: Path,
    history: list[Path],
    *,
    lr: float = 0.05,
    l2: float = 1e-4,
) -> SessionImitationResult:
    model = OnlineOvRClassifier(len(ORACLE_FEATURE_NAMES), lr=lr, l2=l2)
    for path in history:
        for s in build_bar_samples(path):
            model.update(s.features, s.label)

    target_samples = build_bar_samples(target)
    closes = [b.close for b in session_bar_closes(target)]
    cost = round_trip_cost_per_share(sum(closes) / len(closes)) if closes else 0.0
    _, oracle_pnl = oracle_path_states(closes, cost_per_share=cost)

    pairs: list[tuple[int, int]] = []
    for s in target_samples:
        pred = model.predict(s.features)
        pairs.append((pred, s.label))
        model.update(s.features, s.label)

    acc = _accuracy(pairs)
    flat_bl = _flat_baseline_accuracy(target_samples)
    maj_bl = _majority_baseline_accuracy(target_samples)
    return SessionImitationResult(
        session=target.stem.replace("_tape", ""),
        n_bars=len(target_samples),
        oracle_pnl=oracle_pnl,
        model_acc=acc,
        flat_baseline=flat_bl,
        majority_baseline=maj_bl,
        lift_vs_flat=acc - flat_bl if acc == acc else float("nan"),
        lift_vs_majority=acc - maj_bl if acc == acc else float("nan"),
    )


def _verdict(avg_lift_flat: float, avg_lift_maj: float, total_oracle: float) -> str:
    if total_oracle < 5.0:
        return (
            "MARKET: oracle ceiling is thin across sessions — even perfect prediction "
            "may not cover live friction. Collect more tapes; check busier days."
        )
    if avg_lift_flat < 0.03 and avg_lift_maj < 0.03:
        return (
            "DEAD HORSE (features): model does not beat naive baselines. Breadth/EMA "
            "features do not predict optimal actions — tune rules or add features "
            "before any ML strategy."
        )
    if avg_lift_flat < 0.08:
        return (
            "WEAK SIGNAL: features carry a hint but not enough for deployment. Keep "
            "recording sessions; revisit when N≥30 days."
        )
    return (
        "SIGNAL EXISTS: features predict oracle actions better than baselines. "
        "Worth building predict-then-position prototype offline."
    )


def main() -> None:
    load_dotenv_file()
    ap = argparse.ArgumentParser(description="Oracle-imitation dead-horse test")
    ap.add_argument("--target", type=Path, default=None)
    args = ap.parse_args()

    tapes = sorted(tape_dir().glob("*_tape.jsonl"), key=lambda p: p.stem)
    if not tapes:
        raise SystemExit("no tapes found")

    print("=== Oracle imitation (dead-horse test) ===")
    print(f"Features: {', '.join(ORACLE_FEATURE_NAMES)}")
    print(f"Label: optimal {{flat,long,short}} per 5m bar (DP oracle, cost-aware)")
    print()
    hdr = "%-9s %5s %8s %7s %7s %7s %6s %6s" % (
        "session", "bars", "oracle", "model", "flatBL", "majBL", "Δflat", "Δmaj"
    )
    print(hdr)
    print("-" * len(hdr))

    results: list[SessionImitationResult] = []
    for i, target in enumerate(tapes):
        if len(session_bar_closes(target)) < 2:
            continue
        history = tapes[:i]
        r = evaluate_session(target, history)
        results.append(r)
        print(
            "%-9s %5d %+8.2f %6.1f%% %6.1f%% %6.1f%% %+5.1f%% %+5.1f%%"
            % (
                r.session,
                r.n_bars,
                r.oracle_pnl,
                r.model_acc * 100,
                r.flat_baseline * 100,
                r.majority_baseline * 100,
                r.lift_vs_flat * 100,
                r.lift_vs_majority * 100,
            )
        )

    if not results:
        raise SystemExit("no results")

    # Skip first session (often tiny / no EMA warmup) from averages
    scored = [r for r in results if r.n_bars >= 10]
    avg_lift_flat = sum(r.lift_vs_flat for r in scored) / len(scored)
    avg_lift_maj = sum(r.lift_vs_majority for r in scored) / len(scored)
    total_oracle = sum(r.oracle_pnl for r in scored)

    print("-" * len(hdr))
    print(
        f"Avg lift vs always-flat: {avg_lift_flat*100:+.1f}%   "
        f"vs majority: {avg_lift_maj*100:+.1f}%   "
        f"total oracle ceiling: {total_oracle:+.2f}/sh"
    )
    print()
    print("VERDICT:", _verdict(avg_lift_flat, avg_lift_maj, total_oracle))


if __name__ == "__main__":
    main()
