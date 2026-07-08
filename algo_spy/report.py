"""Trade persistence + end-of-session summary."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import TYPE_CHECKING

from core.reporting import print_summary as print_shared_summary
from .strategy import ClosedTrade

if TYPE_CHECKING:
    from .strategy import Strategy

DEFAULT_TRADES_LOG = Path(__file__).resolve().parent / "trades.jsonl"


def write_trades_log(strat: "Strategy", path: Path = DEFAULT_TRADES_LOG) -> int:
    """Overwrite path with one JSON object per closed trade. Returns count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for t in strat.account.closed:
            f.write(json.dumps(dataclasses.asdict(t)) + "\n")
    return len(strat.account.closed)


def flatten_open_position(strat: "Strategy", reason: str = "session_end_flatten") -> None:
    account = strat.account
    pos = account.position
    if pos is None:
        return
    ref = strat.last_price or pos.entry_price
    side = "SELL" if pos.qty > 0 else "BUY"
    fill_px, spread, comm = strat._simulate_fill(side, ref, pos.qty)
    if pos.qty > 0:
        account.cash += pos.qty * fill_px - comm
    else:
        account.cash -= (-pos.qty) * fill_px + comm
    account.total_commission += comm
    strat._record_fill_spread(spread)
    trade_comm = pos.entry_commission + comm
    pnl = (fill_px - pos.entry_price) * pos.qty - trade_comm
    meta = strat._fill_meta()
    strat.last_fill_meta = meta
    account.closed.append(
        ClosedTrade(
            entry_ts=pos.entry_ts,
            exit_ts=pos.entry_ts,
            qty=pos.qty,
            entry_price=pos.entry_price,
            exit_price=fill_px,
            pnl=pnl,
            reason=reason,
            entry_spread=pos.entry_spread,
            exit_spread=spread,
            commission=trade_comm,
            entry_score=pos.entry_score,
            entry_short=pos.entry_short,
            entry_medium=pos.entry_medium,
            entry_divergence=pos.entry_divergence,
            exit_score=meta.get("breadth_score"),
            exit_short=meta.get("short_breadth"),
            exit_medium=meta.get("medium_breadth"),
            exit_divergence=meta.get("divergence"),
        )
    )
    account.position = None


def print_summary(strat: "Strategy", algo_id: str, log_path: Path = DEFAULT_TRADES_LOG) -> None:
    flatten_open_position(strat, reason="session_end_flatten")
    print_shared_summary(strat, algo_id=algo_id, log_path=log_path)
