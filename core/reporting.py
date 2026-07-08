from __future__ import annotations

import dataclasses
import json
from math import gcd
from pathlib import Path


def flatten_open_position(strat, closed_trade_cls, reason: str = "session_end_flatten") -> None:
    account = strat.account
    pos = account.position
    if pos is None:
        return
    last_px = strat.last_price or pos.entry_price
    pnl = (last_px - pos.entry_price) * pos.qty
    if pos.qty > 0:
        account.cash += pos.qty * last_px
    else:
        account.cash -= (-pos.qty) * last_px
    account.closed.append(
        closed_trade_cls(
            entry_ts=pos.entry_ts,
            exit_ts=pos.entry_ts,
            qty=pos.qty,
            entry_price=pos.entry_price,
            exit_price=last_px,
            pnl=pnl,
            reason=reason,
        )
    )
    account.position = None


def write_trades_log(strat, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for trade in strat.account.closed:
            f.write(json.dumps(dataclasses.asdict(trade)) + "\n")
    return len(strat.account.closed)


def trade_expectancy(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """E[per trade] = win_rate * avg_win + (1 - win_rate) * avg_loss (avg_loss typically < 0)."""
    return win_rate * avg_win + (1.0 - win_rate) * avg_loss


def risk_reward_ratio(avg_win: float, avg_loss: float) -> tuple[str, float] | None:
    """Return risk:reward as x:y and breakeven win rate (%).

    risk:reward uses |avg_loss| : avg_win. Breakeven = risk / (risk + reward).
    """
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


def print_summary(strat, algo_id: str, log_path: Path) -> None:
    account = strat.account
    last = strat.last_price or 0.0
    equity = account.equity(last)
    realized = account.realized_pnl()
    unrealized = 0.0 if account.position is None else (last - account.position.entry_price) * account.position.qty
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
        print(f"  win rate:          {len(wins) / len(account.closed) * 100:.1f}% "
              f"({len(wins)}W / {len(losses)}L)")
        if wins:
            avg_win = sum(t.pnl for t in wins) / len(wins)
            print(f"  avg win:           {avg_win:+.2f}")
        else:
            avg_win = 0.0
        if losses:
            avg_loss = sum(t.pnl for t in losses) / len(losses)
            print(f"  avg loss:          {avg_loss:+.2f}")
        else:
            avg_loss = 0.0
        win_rate = len(wins) / len(account.closed)
        expectancy = trade_expectancy(win_rate, avg_win, avg_loss)
        print(f"  expectancy/trade:  {expectancy:+.2f}")
        rr = risk_reward_ratio(avg_win, avg_loss) if wins and losses else None
        if rr is not None:
            ratio, breakeven_pct = rr
            print(f"  risk:reward:       {ratio}  (breakeven win rate {breakeven_pct:.1f}%)")
        total_comm = getattr(account, "total_commission", 0.0)
        if total_comm > 0:
            print(f"  total commission:  {total_comm:.2f}")
        fill_spreads = getattr(account, "fill_spreads", None)
        if fill_spreads:
            avg_spread = sum(fill_spreads) / len(fill_spreads)
            print(f"  avg SPY spread:    {avg_spread:.4f}  ({len(fill_spreads)} fills)")
        reasons: dict[str, int] = {}
        for trade in account.closed:
            reasons[trade.reason] = reasons.get(trade.reason, 0) + 1
        print("  exit reasons:      " + ", ".join(f"{key}={value}" for key, value in reasons.items()))
    if account.position is not None:
        pos = account.position
        print(f"  open position:     qty={pos.qty} entry={pos.entry_price:.4f} last={last:.4f}")
    print(f"  trades log:        {log_path} ({rows} rows)")
    print("═" * 64)
