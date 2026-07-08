"""Breadth-confirmed leader-continuation momentum strategy.

Built on hlt_algo_feed (async AlgoFeed) — the package owns connect / subscribe /
reconnect; this file is pure decision logic + a paper account. Prints only.

Rules (see chat writeup for rationale):
  1. Market filter   — only long when buy_pct_5m and the market high/low rate
                        ratio (from market_summary) both confirm a bullish tape.
  2. Persistence     — a symbol only qualifies once it's a *cumulative* leader
                        (high_count) AND currently hot in the trailing window
                        we track ourselves (the feed gives raw events, not a
                        pre-aggregated recent-window count, so we roll our own).
  3. Oscillation filter — drop symbols hitting both new_high and new_low in the
                        trailing window (choppy, not trending — this is what
                        flagged DASH as a non-candidate despite high crossing counts).
  4. Entry           — new_high on a qualified symbol with volume_spike=True.
  5. Exit            — the symbol itself starts stacking new_lows (reversal), or
                        the market-level breadth filter breaks (kill switch).

Run:  python breadth_momentum.py
Env:  HLT_URL (default ws://127.0.0.1:7412)
"""
from __future__ import annotations

import asyncio
import os
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from hlt_algo_feed import AlgoFeed

URL = os.environ.get("HLT_URL", "ws://127.0.0.1:7412")

RECENT_WINDOW = timedelta(minutes=5)
PERSIST_MIN_CUM = 10          # min cumulative high_count to call a symbol a "leader"
PERSIST_MIN_RECENT = 3        # min recent new-high hits to call it "hot" right now
RECENT_LOW_EXIT_THRESHOLD = 3 # exit once a held symbol stacks this many recent lows

BREADTH_ENTER_BUY_PCT = 65.0  # buy_pct_5m must clear this to allow new entries
BREADTH_EXIT_BUY_PCT = 50.0   # buy_pct_5m below this flattens everything
BREADTH_MIN_HL_RATIO = 2.0    # market_high_rate_5m / market_low_rate_5m floor


@dataclass
class SymbolState:
    cum_high: int = 0
    cum_low: int = 0
    recent: deque = field(default_factory=deque)  # (ts, "high"|"low")

    def recent_counts(self, now: datetime) -> tuple[int, int]:
        cutoff = now - RECENT_WINDOW
        while self.recent and self.recent[0][0] < cutoff:
            self.recent.popleft()
        highs = sum(1 for _, side in self.recent if side == "high")
        lows = sum(1 for _, side in self.recent if side == "low")
        return highs, lows


class BreadthMomentum:
    def __init__(self, feed: AlgoFeed):
        self.feed = feed
        self.symbols: dict[str, SymbolState] = defaultdict(SymbolState)
        self.positions: dict[str, float] = {}  # symbol -> entry price
        self.realized = 0.0
        self.buy_pct_5m: float | None = None
        self.high_rate_5m: int | None = None
        self.low_rate_5m: int | None = None

    def breadth_ok(self) -> bool:
        if self.buy_pct_5m is None or not self.high_rate_5m:
            return False
        ratio = self.high_rate_5m / max(self.low_rate_5m or 0, 1)
        return self.buy_pct_5m >= BREADTH_ENTER_BUY_PCT and ratio >= BREADTH_MIN_HL_RATIO

    def breadth_broken(self) -> bool:
        return self.buy_pct_5m is not None and self.buy_pct_5m < BREADTH_EXIT_BUY_PCT

    def qualifies(self, symbol: str, now: datetime) -> bool:
        st = self.symbols[symbol]
        recent_high, recent_low = st.recent_counts(now)
        leader = st.cum_high >= PERSIST_MIN_CUM
        hot = recent_high >= PERSIST_MIN_RECENT
        choppy = recent_high > 0 and recent_low > 0
        return leader and hot and not choppy

    async def __call__(self, ev) -> None:
        now = datetime.fromisoformat(ev.ts.replace("Z", "+00:00"))

        if ev.event == "market_summary":
            self.buy_pct_5m = ev.buy_pct_5m
            self.high_rate_5m = ev.market_high_rate_5m
            self.low_rate_5m = ev.market_low_rate_5m
            if self.breadth_broken():
                await self._flatten_all(reason="breadth flipped below threshold")
            return

        st = self.symbols[ev.symbol]
        if "high" in ev.event:
            st.cum_high = ev.high_count
            st.recent.append((now, "high"))
        if "low" in ev.event:
            st.cum_low = ev.low_count
            st.recent.append((now, "low"))

        # Exit check runs before entry check so a reversing held name never
        # gets re-evaluated as a fresh entry in the same event.
        if ev.symbol in self.positions and "low" in ev.event:
            _, recent_low = st.recent_counts(now)
            if recent_low >= RECENT_LOW_EXIT_THRESHOLD:
                await self._exit(ev.symbol, ev.last_price, reason="stacking new lows")
            return

        if (
            ev.symbol not in self.positions
            and "high" in ev.event
            and ev.volume_spike
            and self.breadth_ok()
            and self.qualifies(ev.symbol, now)
        ):
            await self._enter(ev.symbol, ev.last_price)

    async def _enter(self, symbol: str, price: float | None) -> None:
        if price is None:
            return
        self.positions[symbol] = price
        # SUBSCRIBE_WATCH replaces the whole set — resend it with every open
        # position so price_update ticks keep flowing for stop/target tracking.
        await self.feed.watch(list(self.positions))
        print(f"ENTER {symbol} @ {price:.2f}", flush=True)

    async def _exit(self, symbol: str, price: float | None, reason: str) -> None:
        entry = self.positions.pop(symbol)
        pnl = (price - entry) if price is not None else 0.0
        self.realized += pnl
        print(
            f"EXIT  {symbol} @ {price:.2f}  ({reason})  "
            f"pnl {pnl:+.2f}  total {self.realized:+.2f}",
            flush=True,
        )
        await self.feed.watch(list(self.positions))

    async def _flatten_all(self, reason: str) -> None:
        for symbol in list(self.positions):
            entry = self.positions.pop(symbol)
            print(f"FLATTEN {symbol} (entry {entry:.2f}) — {reason}", flush=True)
        await self.feed.watch([])


async def main() -> None:
    async with AlgoFeed(URL) as feed:
        strat = BreadthMomentum(feed)
        await feed.subscribe_summary(True)
        async for ev in feed:
            await strat(ev)


if __name__ == "__main__":
    asyncio.run(main())
