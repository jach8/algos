"""Trade persistence + end-of-session summary for multi-symbol momentum."""
from __future__ import annotations

import dataclasses
import json
import time
from datetime import datetime, timezone
from math import gcd
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .strategy import Strategy

DEFAULT_TRADES_LOG = Path(__file__).resolve().parent / "trades.jsonl"


def _iso(ts_sec: float) -> str:
    dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def write_trades_log(strat: "Strategy", path: Path = DEFAULT_TRADES_LOG) -> int:
    """Overwrite path with one JSON object per closed trade. Returns count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for t in strat.account.closed:
            f.write(json.dumps(dataclasses.asdict(t)) + "\n")
    return len(strat.account.closed)


def flatten_open_positions(strat: "Strategy", reason: str = "session_end_flatten") -> None:
    """Mark-to-market flatten every open position with realistic fill costs."""
    ts_sec = time.time()
    ts = _iso(ts_sec)
    for symbol in list(strat.account.positions):
        ref = strat.last_prices.get(symbol) or strat.account.positions[symbol].entry_price
        strat._exit(symbol, ref, ts_sec, ts, reason)


def _trade_expectancy(win_rate: float, avg_win: float, avg_loss: float) -> float:
    return win_rate * avg_win + (1.0 - win_rate) * avg_loss


def _risk_reward_ratio(avg_win: float, avg_loss: float) -> tuple[str, float] | None:
    risk = abs(avg_loss)
    reward = avg_win
    if risk <= 0 or reward <= 0:
        return None
    risk_c = int(round(risk * 100))
    reward_c = int(round(reward * 100))
    if risk_c <= 0 or reward_c <= 0:
        return None
    divisor = gcd(risk_c, reward_c) or 1
    breakeven_pct = risk / (risk + reward) * 100.0
    return f"{risk_c // divisor}:{reward_c // divisor}", breakeven_pct


def print_summary(strat: "Strategy", algo_id: str, log_path: Path = DEFAULT_TRADES_LOG) -> None:
    flatten_open_positions(strat, reason="session_end_flatten")
    account = strat.account
    equity = account.equity(strat.last_prices)
    realized = account.realized_pnl()
    unrealized = 0.0
    rows = write_trades_log(strat, log_path)

    print()
    print("═" * 64)
    print(f" SUMMARY — {algo_id}")
    print("═" * 64)
    print(f"  trades closed:     {len(account.closed)}")
    print(f"  realized P&L:      {realized:+.2f}")
    print(f"  unrealized P&L:    {unrealized:+.2f}")
    print(f"  starting cash:     {account.starting_cash:.2f}")
    print(f"  ending equity:     {equity:.2f}")
    if account.starting_cash > 0:
        print(f"  return:            {(equity / account.starting_cash - 1.0) * 100.0:+.2f}%")
    if account.closed:
        wins = [t for t in account.closed if t.pnl > 0]
        losses = [t for t in account.closed if t.pnl <= 0]
        print(
            f"  win rate:          {len(wins) / len(account.closed) * 100:.1f}% "
            f"({len(wins)}W / {len(losses)}L)"
        )
        avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0.0
        if wins:
            print(f"  avg win:           {avg_win:+.2f}")
        if losses:
            print(f"  avg loss:          {avg_loss:+.2f}")
        win_rate = len(wins) / len(account.closed)
        expectancy = _trade_expectancy(win_rate, avg_win, avg_loss)
        print(f"  expectancy/trade:  {expectancy:+.2f}")
        rr = _risk_reward_ratio(avg_win, avg_loss) if wins and losses else None
        if rr is not None:
            ratio, breakeven_pct = rr
            print(f"  risk:reward:       {ratio}  (breakeven win rate {breakeven_pct:.1f}%)")
        if account.total_commission > 0:
            print(f"  total commission:  {account.total_commission:.2f}")
        if account.fill_spreads:
            avg_spread = sum(account.fill_spreads) / len(account.fill_spreads)
            print(f"  avg fill spread:   {avg_spread:.4f}  ({len(account.fill_spreads)} fills)")
        by_symbol: dict[str, int] = {}
        for trade in account.closed:
            by_symbol[trade.symbol] = by_symbol.get(trade.symbol, 0) + 1
        print(
            "  symbols traded:    "
            + ", ".join(f"{sym}={n}" for sym, n in sorted(by_symbol.items()))
        )
        reasons: dict[str, int] = {}
        for trade in account.closed:
            reasons[trade.reason] = reasons.get(trade.reason, 0) + 1
        print(
            "  exit reasons:      "
            + ", ".join(f"{key}={value}" for key, value in reasons.items())
        )
    print(f"  trades log:        {log_path} ({rows} rows)")
    print("═" * 64)
