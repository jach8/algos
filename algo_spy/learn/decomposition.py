"""Entry/exit oracle decomposition — localize where P&L leaks vs the ceiling."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.time_utils import parse_iso

from ..bars import BAR_5M_SEC, BarAggregator
from ..env_config import load_dotenv_file
from ..get_previous_data import warmup_strategy_from_yahoo
from ..main import handle_tape_event
from ..report import flatten_open_position
from ..strategy import Strategy, _iso
from ..tape_recorder import load_tape_log, row_to_tape_event, tape_dir
from .costs import round_trip_cost_per_share
from .oracle import buy_and_hold_per_share, oracle_pnl_per_share
from .oracle_trace import oracle_entry_events


@dataclass(frozen=True)
class BarClose:
    ts_sec: float
    close: float
    idx: int


@dataclass
class DecompositionResult:
    session: str
    n_bars: int
    cost_per_share: float
    full_oracle: float
    intraday_hold: float  # session open close -> last close (NOT prior-close based)
    first_close: float
    last_close: float
    prior_close: float | None  # last recorded price of prior session (gap-aware)
    entry_oracle_strategy_exit: float
    strategy_entry_exit_oracle: float
    strategy_realized: float
    n_oracle_entries: int
    n_strategy_trades: int
    n_entry_oracle_trades: int

    @property
    def intraday_pct(self) -> float:
        return (self.last_close / self.first_close - 1.0) * 100.0 if self.first_close else 0.0

    @property
    def daily_close_to_close(self) -> float | None:
        """Last close minus prior session's last recorded price (captures overnight gap)."""
        if self.prior_close is None:
            return None
        return self.last_close - self.prior_close

    @property
    def daily_pct(self) -> float | None:
        if self.prior_close is None or self.prior_close == 0:
            return None
        return (self.last_close / self.prior_close - 1.0) * 100.0


def session_bar_closes(path: Path) -> list[BarClose]:
    bars = BarAggregator(period_sec=BAR_5M_SEC)
    out: list[BarClose] = []
    idx = 0
    for row in load_tape_log(path):
        ev = row_to_tape_event(row)
        if ev.get("symbol") != "SPY":
            continue
        ts = ev.get("ts") or ""
        px = ev.get("last_price")
        if not ts or px is None:
            continue
        ts_sec = parse_iso(ts)
        bars.on_tick(ts_sec, float(px))
        for bar in bars.pop_closed():
            bar_ts = bar.bucket_epoch * BAR_5M_SEC + BAR_5M_SEC
            out.append(BarClose(bar_ts, bar.close, idx))
            idx += 1
    return out


def _best_exit_pnl(
    closes: list[float], entry_idx: int, side: int, cost: float
) -> float:
    """Perfect exit on bar closes after a fixed entry (one round-trip cost)."""
    entry_px = closes[entry_idx]
    best = float("-inf")
    for j in range(entry_idx + 1, len(closes)):
        gross = (closes[j] - entry_px) * side
        pnl = gross - cost
        if pnl > best:
            best = pnl
    return 0.0 if best == float("-inf") else best


def _bar_index_for_ts(bars: list[BarClose], ts_sec: float) -> int | None:
    """Latest bar whose close time is <= ts_sec."""
    idx: int | None = None
    for b in bars:
        if b.ts_sec <= ts_sec + 1e-6:
            idx = b.idx
        else:
            break
    return idx


def _replay_strategy(path: Path, symbol: str = "SPY") -> Strategy:
    strat = Strategy(symbol=symbol)
    try:
        warmup_strategy_from_yahoo(strat, symbol, max_bars=120)
    except Exception:
        pass
    rows = load_tape_log(path)
    for row in rows:
        ev = row_to_tape_event(row)
        if ev.get("ts"):
            handle_tape_event(strat, ev, record_tape=False, log_breadth=False)
    flatten_open_position(strat, reason="session_end_flatten")
    return strat


def _disable_entries(strat: Strategy) -> Callable[[], None]:
    original = strat._try_entry_at_bar_close

    def _blocked(*_a: object, **_k: object) -> list[dict]:
        return []

    strat._try_entry_at_bar_close = _blocked  # type: ignore[method-assign]

    def restore() -> None:
        strat._try_entry_at_bar_close = original  # type: ignore[method-assign]

    return restore


def _simulate_trade_from_entry(
    path: Path,
    entry_ts_sec: float,
    side: int,
    ref_price: float,
    symbol: str = "SPY",
) -> tuple[float, float]:
    """Replay from oracle entry with strategy exits; return (P&L/share, exit_ts_sec)."""
    strat = Strategy(symbol=symbol)
    try:
        warmup_strategy_from_yahoo(strat, symbol, max_bars=120)
    except Exception:
        pass
    restore = _disable_entries(strat)
    qty = 1.0 if side > 0 else -1.0
    rows = load_tape_log(path)
    entered = False
    try:
        for row in rows:
            ev = row_to_tape_event(row)
            ts = ev.get("ts") or ""
            if not ts:
                continue
            ts_sec = parse_iso(ts)
            if not entered:
                if ts_sec < entry_ts_sec:
                    # Warm state machines up to the entry bar.
                    handle_tape_event(strat, ev, record_tape=False, log_breadth=False)
                    continue
                strat._enter(qty, ref_price, entry_ts_sec, _iso(entry_ts_sec), "oracle_entry")
                entered = True
            handle_tape_event(strat, ev, record_tape=False, log_breadth=False)
            if strat.account.position is None and entered:
                exit_ts = parse_iso(strat.account.closed[-1].exit_ts)
                return strat.account.closed[-1].pnl / abs(qty), exit_ts
    finally:
        restore()

    if not entered:
        return 0.0, entry_ts_sec
    if strat.account.position is not None:
        flatten_open_position(strat, reason="session_end_flatten")
    if not strat.account.closed:
        return 0.0, entry_ts_sec
    exit_ts = parse_iso(strat.account.closed[-1].exit_ts)
    return strat.account.closed[-1].pnl / abs(qty), exit_ts


def entry_oracle_strategy_exit_pnl(
    path: Path,
    bars: list[BarClose],
    entries: list[tuple[int, int]],
) -> tuple[float, int]:
    """Perfect entries (oracle path), real strategy exits."""
    total = 0.0
    n_trades = 0
    cursor_ts = 0.0

    for entry_idx, side in entries:
        if entry_idx >= len(bars):
            continue
        entry_bar = bars[entry_idx]
        if entry_bar.ts_sec < cursor_ts:
            continue
        pnl, exit_ts = _simulate_trade_from_entry(
            path, entry_bar.ts_sec, side, entry_bar.close
        )
        total += pnl
        n_trades += 1
        cursor_ts = exit_ts

    return total, n_trades


def strategy_entry_exit_oracle_pnl(
    strat: Strategy,
    bars: list[BarClose],
    closes: list[float],
    cost: float,
) -> float:
    """Real strategy entries, perfect exits on 5m closes (per-share, one share each)."""
    total = 0.0
    n = 0
    for trade in strat.account.closed:
        entry_ts = parse_iso(trade.entry_ts)
        side = 1 if trade.qty > 0 else -1
        idx = _bar_index_for_ts(bars, entry_ts)
        if idx is None:
            continue
        total += _best_exit_pnl(closes, idx, side, cost)
        n += 1
    return total if n == 0 else total  # sum of per-share perfect exits across trades


def strategy_realized_per_share(strat: Strategy) -> float:
    if not strat.account.closed:
        return 0.0
    return sum(t.pnl / abs(t.qty) for t in strat.account.closed)


def _prior_session_last_price(path: Path, symbol: str = "SPY") -> float | None:
    """Last recorded `symbol` price from the most recent prior *_tape.jsonl (gap-aware)."""
    tapes = sorted(path.parent.glob("*_tape.jsonl"), key=lambda p: p.stem)
    priors = [p for p in tapes if p.stem < path.stem]
    if not priors:
        return None
    last_px: float | None = None
    for row in load_tape_log(priors[-1]):
        ev = row_to_tape_event(row)
        if ev.get("symbol") != symbol:
            continue
        px = ev.get("last_price")
        if px is not None:
            last_px = float(px)
    return last_px


def run_decomposition(path: Path, *, symbol: str = "SPY") -> DecompositionResult:
    session = path.stem.replace("_tape", "")
    bars = session_bar_closes(path)
    closes = [b.close for b in bars]
    avg_close = sum(closes) / len(closes) if closes else 0.0
    cost = round_trip_cost_per_share(avg_close)

    full = oracle_pnl_per_share(closes, cost_per_share=cost)
    bh = buy_and_hold_per_share(closes)
    prior_close = _prior_session_last_price(path, symbol=symbol)
    entries = oracle_entry_events(closes, cost_per_share=cost)

    entry_oracle_pnl, n_eo = entry_oracle_strategy_exit_pnl(path, bars, entries)

    strat = _replay_strategy(path, symbol=symbol)
    exit_oracle_pnl = strategy_entry_exit_oracle_pnl(strat, bars, closes, cost)
    real = strategy_realized_per_share(strat)

    return DecompositionResult(
        session=session,
        n_bars=len(closes),
        cost_per_share=cost,
        full_oracle=full,
        intraday_hold=bh,
        first_close=closes[0] if closes else 0.0,
        last_close=closes[-1] if closes else 0.0,
        prior_close=prior_close,
        entry_oracle_strategy_exit=entry_oracle_pnl,
        strategy_entry_exit_oracle=exit_oracle_pnl,
        strategy_realized=real,
        n_oracle_entries=len(entries),
        n_strategy_trades=len(strat.account.closed),
        n_entry_oracle_trades=n_eo,
    )


def _print_report(r: DecompositionResult) -> None:
    print(f"=== Oracle decomposition — {r.session} ===")
    print(f"5m bars: {r.n_bars}   round-trip cost/share: {r.cost_per_share:.3f}")
    print()
    if r.daily_close_to_close is not None:
        print(
            f"prior-close→last (gap-aware daily): {r.daily_close_to_close:+.3f} "
            f"({r.daily_pct:+.2f}%)   intraday open→last: {r.intraday_hold:+.3f} "
            f"({r.intraday_pct:+.2f}%)"
        )
    print()
    print(f"{'Run':<42} {'P&L/share':>10}  notes")
    print("-" * 72)
    print(
        f"{'Intraday hold (open→last, NOT daily)':<42} {r.intraday_hold:>+10.3f}  "
        f"misses overnight gap"
    )
    print(f"{'Full oracle (perfect entry + exit)':<42} {r.full_oracle:>+10.3f}  long/short, switches")
    print(
        f"{'Entry-oracle + strategy exits':<42} {r.entry_oracle_strategy_exit:>+10.3f}  "
        f"n={r.n_entry_oracle_trades} oracle entries"
    )
    print(
        f"{'Strategy entries + exit-oracle':<42} {r.strategy_entry_exit_oracle:>+10.3f}  "
        f"n={r.n_strategy_trades} strategy entries"
    )
    print(
        f"{'Real strategy (entry + exit)':<42} {r.strategy_realized:>+10.3f}  "
        f"n={r.n_strategy_trades} trades"
    )
    print()
    entry_gap = r.full_oracle - r.entry_oracle_strategy_exit
    exit_gap = r.entry_oracle_strategy_exit - r.strategy_realized
    # Alternative exit gap from strategy entries:
    exit_gap_alt = r.strategy_entry_exit_oracle - r.strategy_realized
    total_gap = r.full_oracle - r.strategy_realized

    print("Gaps (where P&L leaks vs ceiling):")
    print(f"  Exit side  (full → entry-oracle):     {entry_gap:+.3f}  "
          f"({'exits cost more' if entry_gap > 0 else 'exits beat oracle path'})")
    print(f"  Entry side (entry-oracle → real):     {exit_gap:+.3f}  "
          f"({'entries worse than oracle' if exit_gap > 0 else 'entries beat oracle picks'})")
    print(f"  Exit check (strat entry → exit-oracle): {exit_gap_alt:+.3f}  "
          f"({'exits are the leak' if exit_gap_alt > 0 else 'exits are fine'})")
    print(f"  Total gap (full → real):              {total_gap:+.3f}")
    if r.full_oracle > 0:
        print(f"  Capture vs ceiling: {r.strategy_realized / r.full_oracle:5.1%}")


def main() -> None:
    load_dotenv_file()
    ap = argparse.ArgumentParser(description="Entry/exit oracle decomposition for one tape")
    ap.add_argument("--tape", type=Path, default=None, help="*_tape.jsonl (default: latest)")
    ap.add_argument("--symbol", default="SPY")
    args = ap.parse_args()
    tapes = sorted(tape_dir().glob("*_tape.jsonl"), key=lambda p: p.stem)
    if not tapes:
        raise SystemExit("no tapes found")
    path = args.tape or tapes[-1]
    if not path.is_file():
        raise SystemExit(f"tape not found: {path}")
    result = run_decomposition(path, symbol=args.symbol)
    _print_report(result)


if __name__ == "__main__":
    main()
