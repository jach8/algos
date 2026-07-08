"""Live SPY paper-trading client. Connects to ws://127.0.0.1:7412.

v2: breadth-first + 5m bar-close EMA confirmation.
"""
from __future__ import annotations

import argparse
import asyncio

from core.colors import COLORS
from core.feed import run_feed_loop
from core.time_utils import event_est, now_est, parse_iso

from .breadth_log import BreadthLogger
from .env_config import ENV_FILE, load_dotenv_file
from .get_previous_data import warmup_strategy_from_yahoo
from .report import print_summary
from .strategy import ALGO_ID, Strategy
from .tape_recorder import TapeRecorder, recording_enabled

DEFAULT_URL = "ws://127.0.0.1:7412"
_breadth_logger = BreadthLogger()
_tape_recorder = TapeRecorder()


async def run(
    strat: Strategy,
    url: str,
    symbol: str,
    *,
    use_warmup: bool = True,
    warmup_bars: int = 120,
) -> None:
    if use_warmup:
        try:
            n = warmup_strategy_from_yahoo(strat, symbol, max_bars=warmup_bars)
            ready = "ready" if strat.ema.ready() else "warming"
            print(
                f"[algo_spy {now_est()}] Yahoo warmup: {n} bars, EMA {ready}, "
                f"last={strat.last_price}"
            )
        except Exception as exc:
            print(f"[algo_spy {now_est()}] Yahoo warmup skipped: {exc}")
    _breadth_logger.reset()
    tape_path = _tape_recorder.reset()
    if tape_path is not None:
        print(f"[algo_spy {now_est()}] tape replay log: {tape_path}")
    elif not recording_enabled():
        print(f"[algo_spy {now_est()}] tape recording disabled (ALGO_SPY_RECORD_TAPE=0)")
    print(f"[algo_spy {now_est()}] breadth log: {_breadth_logger.path}")
    env_src = f"from {ENV_FILE.name}" if ENV_FILE.is_file() else "defaults"
    print(
        f"[algo_spy {now_est()}] ema_mode={strat.ema_mode} "
        f"flow_mode={strat.throughput.flow_mode} "
        f"entry_cutoff={strat.entry_cutoff_et[0]:02d}:{strat.entry_cutoff_et[1]:02d} ET "
        f"trail arm={strat.trail_activation_pct * 100:.3f}% "
        f"pullback={strat.trail_pct * 100:.3f}% "
        f"min_hold={strat.trail_min_hold_sec:.0f}s ({env_src})"
    )
    try:
        await run_feed_loop(
            url=url,
            symbol=symbol,
            algo_label="algo_spy",
            algo_id=ALGO_ID,
            strat=strat,
            on_tape_event=lambda ev: handle_tape_event(strat, ev),
            on_emit=lambda ev: _log_emit(ev, strat),
        )
    finally:
        _tape_recorder.finalize()
        print_summary(strat, ALGO_ID)


def handle_tape_event(
    strat: Strategy,
    ev: dict,
    *,
    record_tape: bool = True,
    log_breadth: bool = True,
) -> list[dict]:
    """Pure dispatch — used by both the live client and replay backtests."""
    if record_tape and ev.get("type") == "TAPE_EVENT":
        _tape_recorder.record(ev)
    kind = ev.get("event")
    ts = ev.get("ts") or ""
    ts_sec = parse_iso(ts) if ts else 0.0

    out: list[dict] = []
    out.extend(strat.on_tape_market(ev, ts_sec))
    if log_breadth and ts:
        _breadth_logger.maybe_log(strat, ts_sec, ts)

    if ev.get("symbol") == strat.symbol:
        strat.on_quote(ev)
        price = ev.get("last_price")
        if price is not None and kind in (
            "price_update",
            "new_high",
            "new_low",
            "new_high_and_low",
        ):
            out.extend(strat.on_price(ts_sec, float(price)))
    return out


def _fill_label(side: str, strat: Strategy) -> tuple[str, float | None, str | None]:
    pos = strat.account.position
    side_upper = side.upper()
    if side_upper == "BUY":
        if pos is not None and pos.qty > 0:
            return "OPEN LONG", None, None
        closed = strat.account.closed
        if not closed:
            return "CLOSE SHORT", None, None
        return "CLOSE SHORT", closed[-1].pnl, closed[-1].reason
    if pos is not None and pos.qty < 0:
        return "OPEN SHORT", None, None
    closed = strat.account.closed
    if not closed:
        return "CLOSE LONG", None, None
    return "CLOSE LONG", closed[-1].pnl, closed[-1].reason


_EXIT_LABELS: dict[str, tuple[str, str]] = {
    "stop_loss": ("STOP LOSS", "RED"),
    "breadth_medium_roll": ("BREADTH EXIT", "YELLOW"),
    "breadth_short_warning": ("BREADTH WARN EXIT", "YELLOW"),
    "ema_structural": ("EMA STRUCTURAL", "YELLOW"),
    "time_stop": ("TIME STOP", "YELLOW"),
    "session_end_flatten": ("SESSION FLATTEN", "BLUE"),
    # v1 legacy labels (old trades.jsonl)
    "take_profit": ("TAKE PROFIT", "GREEN"),
    "trailing_stop": ("TRAILING STOP", "YELLOW"),
    "below_ema1": ("EMA EXIT", "YELLOW"),
    "above_ema1": ("EMA EXIT", "YELLOW"),
    "throughput_invert": ("THROUGHPUT EXIT", "YELLOW"),
    "5m_stack_break": ("5M STACK BREAK", "YELLOW"),
}


def _format_exit_reason(reason: str | None) -> str:
    if not reason:
        return ""
    label, color = _EXIT_LABELS.get(reason, (reason.upper().replace("_", " "), "YELLOW"))
    return COLORS.print_text(f"[{label}]", color, bold=True)


def _log_emit(ev: dict, strat: Strategy) -> None:
    if ev.get("type") != "ALGO_FILL":
        return
    ts = ev.get("timestamp") or ""
    ts_sec = parse_iso(ts) if ts else 0.0
    if ts:
        _breadth_logger.maybe_log(strat, ts_sec, ts, kind="fill", force=True)
    side = ev.get("side", "")
    px = ev.get("fill_price") or ev.get("price") or strat.last_price
    if px is not None:
        px = round(float(px), 4)
    action, trade_pnl, exit_reason = _fill_label(side, strat)
    running_pnl = strat.account.realized_pnl()
    if action.startswith("OPEN"):
        action_str = COLORS.print_text(action, "GREEN" if "LONG" in action else "RED", bold=True)
    else:
        action_str = COLORS.print_text(action, "YELLOW", bold=True)
    reason_bit = _format_exit_reason(exit_reason) if exit_reason else ""
    meta = strat.last_fill_meta
    if meta:
        reason_bit += (
            f" (score={meta.get('breadth_score', 0):+.0f} "
            f"short={meta.get('short_breadth', 0):+.0f} "
            f"med={meta.get('medium_breadth', 0):+.0f} "
            f"div={meta.get('divergence', '?')})"
        )
    trade_bit = ""
    if trade_pnl is not None:
        trade_bit = f" trade_pnl={trade_pnl:+.2f}"
    print(
        f"[algo_spy {event_est(ev)}] {action_str}{reason_bit} @ {px}{trade_bit} "
        f"running_pnl={running_pnl:+.2f} cash={strat.account.cash:.2f}"
    )


def main() -> None:
    load_dotenv_file()
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument(
        "--no-warmup",
        action="store_true",
        help="skip Yahoo Finance 5m history seed (EMA needs ~10 closed 5m bars)",
    )
    ap.add_argument(
        "--warmup-bars",
        type=int,
        default=120,
        help="max Yahoo 5m bars to replay into EMA (default 24)",
    )
    ap.add_argument(
        "--trail-arm",
        type=float,
        default=None,
        help="trail arm as decimal pct (default from .env ALGO_SPY_TRAIL_ARM or 0.0006)",
    )
    ap.add_argument(
        "--trail-pct",
        type=float,
        default=None,
        help="trail pullback as decimal pct (default from .env ALGO_SPY_TRAIL_PCT or 0.001)",
    )
    ap.add_argument(
        "--ema-mode",
        choices=("breadth", "off", "full"),
        default=None,
        help="breadth=score-led (default), off=no EMA veto, full=legacy EMA filter",
    )
    ap.add_argument(
        "--trail-min-hold",
        type=float,
        default=None,
        help="seconds before trail can exit (default from .env ALGO_SPY_TRAIL_MIN_HOLD_SEC or 60)",
    )
    args = ap.parse_args()
    strat_kw: dict = {"symbol": args.symbol}
    if args.ema_mode is not None:
        strat_kw["ema_mode"] = args.ema_mode
    if args.trail_arm is not None:
        strat_kw["trail_activation_pct"] = args.trail_arm
    if args.trail_pct is not None:
        strat_kw["trail_pct"] = args.trail_pct
    if args.trail_min_hold is not None:
        strat_kw["trail_min_hold_sec"] = args.trail_min_hold
    try:
        strat = Strategy(**strat_kw)
        asyncio.run(
            run(
                strat,
                args.url,
                args.symbol,
                use_warmup=not args.no_warmup,
                warmup_bars=args.warmup_bars,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    print("USER CREATED STRATEGY: algo_spy")
    main()
