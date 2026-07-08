"""DP oracle with backtrace — optimal position path and trade segments."""
from __future__ import annotations

from dataclasses import dataclass

from .oracle import FLAT, LONG, SHORT


@dataclass(frozen=True)
class OracleSegment:
    """One directional leg on the optimal path (bar indices into `closes`)."""

    entry_idx: int
    exit_idx: int
    side: int  # +1 long, -1 short
    pnl_per_share: float


def oracle_path_states(
    closes: list[float], *, cost_per_share: float
) -> tuple[list[tuple[int, int]], float]:
    """Return (bar_index, state) path and total optimal P&L/share."""
    n = len(closes)
    if n < 2:
        return [(0, FLAT)], 0.0

    neg = float("-inf")
    dp: list[list[float]] = [[0.0, neg, neg]]
    parent: list[list[tuple[int, int]]] = [[(-1, -1), (-1, -1), (-1, -1)]]

    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        prev = dp[-1]
        cur = [neg, neg, neg]
        par = [(-1, -1), (-1, -1), (-1, -1)]

        for ps in range(3):
            if prev[ps] > cur[FLAT]:
                cur[FLAT] = prev[ps]
                par[FLAT] = (i - 1, ps)

        if prev[LONG] > neg:
            val = prev[LONG] + d
            if val > cur[LONG]:
                cur[LONG] = val
                par[LONG] = (i - 1, LONG)
        for ps in (FLAT, SHORT):
            if prev[ps] > neg:
                val = prev[ps] - cost_per_share + d
                if val > cur[LONG]:
                    cur[LONG] = val
                    par[LONG] = (i - 1, ps)

        if prev[SHORT] > neg:
            val = prev[SHORT] - d
            if val > cur[SHORT]:
                cur[SHORT] = val
                par[SHORT] = (i - 1, SHORT)
        for ps in (FLAT, LONG):
            if prev[ps] > neg:
                val = prev[ps] - cost_per_share - d
                if val > cur[SHORT]:
                    cur[SHORT] = val
                    par[SHORT] = (i - 1, ps)

        dp.append(cur)
        parent.append(par)

    end_i = n - 1
    best_s = max(range(3), key=lambda s: dp[end_i][s])
    best_pnl = dp[end_i][best_s]

    path: list[tuple[int, int]] = []
    i, s = end_i, best_s
    while i >= 0:
        path.append((i, s))
        pi, ps = parent[i][s]
        if pi < 0:
            break
        i, s = pi, ps
    path.reverse()
    return path, best_pnl


def oracle_entry_events(
    closes: list[float], *, cost_per_share: float
) -> list[tuple[int, int]]:
    """(bar_index, side) each time the optimal path opens a new directional leg."""
    path, _ = oracle_path_states(closes, cost_per_share=cost_per_share)
    entries: list[tuple[int, int]] = []
    prev = FLAT
    for bar_i, state in path:
        if state == LONG and prev != LONG:
            entries.append((bar_i, 1))
        elif state == SHORT and prev != SHORT:
            entries.append((bar_i, -1))
        prev = state
    return entries


def oracle_segments(closes: list[float], *, cost_per_share: float) -> list[OracleSegment]:
    """Non-overlapping directional legs on the optimal path."""
    path, _ = oracle_path_states(closes, cost_per_share=cost_per_share)
    if len(path) < 2:
        return []

    segments: list[OracleSegment] = []
    leg_entry: int | None = None
    leg_side: int | None = None

    def close_leg(exit_idx: int) -> None:
        nonlocal leg_entry, leg_side
        if leg_entry is None or leg_side is None:
            return
        entry_px = closes[leg_entry]
        exit_px = closes[exit_idx]
        gross = (exit_px - entry_px) * leg_side
        pnl = gross - cost_per_share
        segments.append(
            OracleSegment(leg_entry, exit_idx, leg_side, pnl)
        )
        leg_entry = None
        leg_side = None

    prev_state = FLAT
    for bar_i, state in path:
        if state == FLAT:
            if leg_entry is not None:
                close_leg(bar_i)
        elif state == LONG:
            if leg_side == -1:
                close_leg(bar_i)
            if leg_entry is None:
                leg_entry = bar_i
                leg_side = 1
        elif state == SHORT:
            if leg_side == 1:
                close_leg(bar_i)
            if leg_entry is None:
                leg_entry = bar_i
                leg_side = -1
        prev_state = state

    if leg_entry is not None:
        close_leg(path[-1][0])

    return segments
