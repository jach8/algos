"""Live multi-symbol momentum paper client. Connects to ws://127.0.0.1:7412."""
from __future__ import annotations

import argparse
import asyncio

from core.colors import COLORS
from core.feed import run_feed_loop, watch_payload
from core.time_utils import event_est, now_est, parse_iso

from .env_config import ENV_FILE, load_dotenv_file
from .report import print_summary
from .strategy import ALGO_ID, Strategy

DEFAULT_URL = "ws://127.0.0.1:7412"


async def _maybe_refresh_watch(ws, strat: Strategy) -> None:
    if not strat.watch_dirty:
        return
    await ws.send(watch_payload(strat.watch_symbols(), ALGO_ID))
    strat.watch_dirty = False
    print(
        f"[algo_momentum {now_est()}] watch refresh → [{','.join(strat.watch_symbols())}]",
        flush=True,
    )


async def run(strat: Strategy, url: str, symbol: str) -> None:
    env_src = f"from {ENV_FILE.name}" if ENV_FILE.is_file() else "defaults"
    print(
        f"[algo_momentum {now_est()}] max_pos={strat.max_positions} "
        f"cash_frac={strat.cash_fraction:.0%} "
        f"trail arm={strat.trail_activation_pct * 100:.3f}% "
        f"pullback={strat.trail_pct * 100:.3f}% "
        f"min_hold={strat.trail_min_hold_sec:.0f}s "
        f"entry_cutoff={strat.entry_cutoff_et[0]:02d}:{strat.entry_cutoff_et[1]:02d} ET "
        f"({env_src})",
        flush=True,
    )
    try:
        await run_feed_loop(
            url=url,
            symbol=symbol,
            algo_label="algo_momentum",
            algo_id=ALGO_ID,
            strat=strat,
            on_tape_event=lambda ev: handle_tape_event(strat, ev),
            on_emit=lambda ev: _log_emit(ev, strat),
            watch_symbols=strat.watch_symbols(),
            after_tape_event=_maybe_refresh_watch,
        )
    finally:
        print_summary(strat, ALGO_ID)


def handle_tape_event(strat: Strategy, ev: dict) -> list[dict]:
    """Pure dispatch — used by live client and tests."""
    ts = ev.get("ts") or ""
    ts_sec = parse_iso(ts) if ts else 0.0
    return strat.handle_event(ev, ts_sec)


def _fill_label(side: str, symbol: str, strat: Strategy) -> tuple[str, float | None, str | None]:
    side_upper = side.upper()
    if side_upper == "BUY":
        if symbol in strat.account.positions:
            return f"OPEN LONG {symbol}", None, None
        closed = [t for t in strat.account.closed if t.symbol == symbol]
        if not closed:
            return f"BUY {symbol}", None, None
        return f"CLOSE SHORT {symbol}", closed[-1].pnl, closed[-1].reason
    if symbol in strat.account.positions:
        return f"OPEN SHORT {symbol}", None, None
    closed = [t for t in strat.account.closed if t.symbol == symbol]
    if not closed:
        return f"SELL {symbol}", None, None
    return f"CLOSE LONG {symbol}", closed[-1].pnl, closed[-1].reason


_EXIT_LABELS: dict[str, tuple[str, str]] = {
    "stop_loss": ("STOP LOSS", "RED"),
    "trailing_stop": ("TRAILING STOP", "YELLOW"),
    "stacking_lows": ("STACKING LOWS", "YELLOW"),
    "breadth_kill_switch": ("BREADTH KILL", "RED"),
    "session_end_flatten": ("SESSION FLATTEN", "BLUE"),
    "momentum_new_high": ("MOMENTUM HIGH", "GREEN"),
}


def _format_exit_reason(reason: str | None) -> str:
    if not reason:
        return ""
    label, color = _EXIT_LABELS.get(reason, (reason.upper().replace("_", " "), "YELLOW"))
    return COLORS.print_text(f"[{label}]", color, bold=True)


def _log_emit(ev: dict, strat: Strategy) -> None:
    if ev.get("type") != "ALGO_FILL":
        return
    side = ev.get("side", "")
    symbol = (ev.get("symbol") or "?").upper()
    px = ev.get("fill_price") or ev.get("price") or strat.last_prices.get(symbol)
    if px is not None:
        px = round(float(px), 4)
    action, trade_pnl, exit_reason = _fill_label(side, symbol, strat)
    running_pnl = strat.account.realized_pnl()
    if action.startswith("OPEN"):
        action_str = COLORS.print_text(action, "GREEN", bold=True)
    else:
        action_str = COLORS.print_text(action, "YELLOW", bold=True)
    reason_bit = _format_exit_reason(exit_reason) if exit_reason else ""
    meta = strat.last_fill_meta
    if meta:
        buy = meta.get("buy_pct_5m")
        buy_s = f"{buy:.0f}" if isinstance(buy, (int, float)) else "?"
        reason_bit += (
            f" (buy%={buy_s} "
            f"hi={meta.get('high_rate_5m', '?')} "
            f"lo={meta.get('low_rate_5m', '?')} "
            f"open={meta.get('open_positions', '?')})"
        )
    trade_bit = ""
    if trade_pnl is not None:
        trade_bit = f" trade_pnl={trade_pnl:+.2f}"
    print(
        f"[algo_momentum {event_est(ev)}] {action_str}{reason_bit} @ {px}{trade_bit} "
        f"running_pnl={running_pnl:+.2f} cash={strat.account.cash:.2f}",
        flush=True,
    )


def main() -> None:
    load_dotenv_file()
    ap = argparse.ArgumentParser(description="Realtime multi-symbol momentum paper trader")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument(
        "--symbol",
        default="SPY",
        help="seed watch symbol (positions add names dynamically)",
    )
    ap.add_argument("--max-positions", type=int, default=None)
    ap.add_argument("--cash-fraction", type=float, default=None)
    ap.add_argument("--trail-arm", type=float, default=None)
    ap.add_argument("--trail-pct", type=float, default=None)
    ap.add_argument("--trail-min-hold", type=float, default=None)
    args = ap.parse_args()

    strat_kw: dict = {"symbol": args.symbol.upper()}
    if args.max_positions is not None:
        strat_kw["max_positions"] = args.max_positions
    if args.cash_fraction is not None:
        strat_kw["cash_fraction"] = args.cash_fraction
    if args.trail_arm is not None:
        strat_kw["trail_activation_pct"] = args.trail_arm
    if args.trail_pct is not None:
        strat_kw["trail_pct"] = args.trail_pct
    if args.trail_min_hold is not None:
        strat_kw["trail_min_hold_sec"] = args.trail_min_hold

    try:
        strat = Strategy(**strat_kw)
        asyncio.run(run(strat, args.url, args.symbol.upper()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    print("USER CREATED STRATEGY: algo_momentum")
    main()
