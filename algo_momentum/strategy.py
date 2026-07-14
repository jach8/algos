"""Realtime leader-continuation momentum across names printing new highs.

Trades individual symbols making moves on the HighLowTicker tape:
  1. Market filter — only long when buy_pct_5m + HL rate ratio confirm a bullish tape
  2. Persistence — cumulative high leader + hot in a recent window
  3. Oscillation filter — skip names printing both highs and lows in the window
  4. Entry — new_high + volume_spike on a qualified symbol
  5. Exit — stacking lows, stop, trail, breadth kill-switch, session flatten

Paper fills use the same commission / slippage / spread model as algo_spy.
"""
from __future__ import annotations

import os
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .execution import COMMISSION_PER_SHARE, commission_for_shares, fill_price

ALGO_ID = "momentum_leaders_v1"

RECENT_WINDOW = timedelta(minutes=5)
PERSIST_MIN_CUM = 10
PERSIST_MIN_RECENT = 3
RECENT_LOW_EXIT_THRESHOLD = 3

BREADTH_ENTER_BUY_PCT = 65.0
BREADTH_EXIT_BUY_PCT = 50.0
BREADTH_MIN_HL_RATIO = 2.0

DEFAULT_MAX_POSITIONS = 5
DEFAULT_CASH_FRACTION = 0.20  # of free cash per new entry
STOP_PCT = 0.04
DEFAULT_TRAIL_ACTIVATION_PCT = 0.004  # 0.40% — above round-trip commission on names
DEFAULT_TRAIL_PCT = 0.002  # 0.20% pullback from peak once armed
DEFAULT_TRAIL_MIN_HOLD_SEC = 60.0
REENTRY_COOLDOWN_SEC = 300.0

TZ_ET = ZoneInfo("America/New_York")
SESSION_CLOSE_ET = (16, 0)
SESSION_OPEN_ET = (9, 30)
DEFAULT_ENTRY_CUTOFF_ET = (15, 30)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    raw = raw.split("#", 1)[0].strip()
    return float(raw)


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, float(default)))


def _default_trail_arm() -> float:
    return _env_float("ALGO_MOM_TRAIL_ARM", DEFAULT_TRAIL_ACTIVATION_PCT)


def _default_trail_pct() -> float:
    return _env_float("ALGO_MOM_TRAIL_PCT", DEFAULT_TRAIL_PCT)


def _default_trail_min_hold() -> float:
    return _env_float("ALGO_MOM_TRAIL_MIN_HOLD_SEC", DEFAULT_TRAIL_MIN_HOLD_SEC)


def _default_max_positions() -> int:
    return _env_int("ALGO_MOM_MAX_POSITIONS", DEFAULT_MAX_POSITIONS)


def _default_cash_fraction() -> float:
    return _env_float("ALGO_MOM_CASH_FRACTION", DEFAULT_CASH_FRACTION)


def _default_entry_cutoff_et() -> tuple[int, int]:
    raw = os.environ.get("ALGO_MOM_ENTRY_CUTOFF_ET")
    if raw is not None and raw.strip():
        part = raw.split("#", 1)[0].strip()
        hour_str, minute_str = part.split(":", 1)
        return int(hour_str), int(minute_str)
    return DEFAULT_ENTRY_CUTOFF_ET


def _iso(ts_sec: float) -> str:
    dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


@dataclass
class SymbolState:
    cum_high: int = 0
    cum_low: int = 0
    recent: deque = field(default_factory=deque)  # (ts, "high"|"low")
    last_exit_ts_sec: float | None = None

    def recent_counts(self, now: datetime) -> tuple[int, int]:
        cutoff = now - RECENT_WINDOW
        while self.recent and self.recent[0][0] < cutoff:
            self.recent.popleft()
        highs = sum(1 for _, side in self.recent if side == "high")
        lows = sum(1 for _, side in self.recent if side == "low")
        return highs, lows


@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_ts: str
    entry_ts_sec: float
    entry_spread: float | None = None
    entry_commission: float = 0.0
    trail_extreme: float | None = None
    entry_reason: str = "momentum_high"
    entry_cum_high: int | None = None
    entry_recent_high: int | None = None


@dataclass
class ClosedTrade:
    symbol: str
    entry_ts: str
    exit_ts: str
    qty: float
    entry_price: float
    exit_price: float
    pnl: float
    reason: str
    entry_spread: float | None = None
    exit_spread: float | None = None
    commission: float = 0.0
    entry_reason: str | None = None
    entry_cum_high: int | None = None
    entry_recent_high: int | None = None


@dataclass
class Account:
    starting_cash: float = 10_000.0
    cash: float = 10_000.0
    positions: dict[str, Position] = field(default_factory=dict)
    closed: list[ClosedTrade] = field(default_factory=list)
    total_commission: float = 0.0
    fill_spreads: list[float] = field(default_factory=list)

    def equity(self, last_prices: dict[str, float]) -> float:
        marked = 0.0
        for symbol, pos in self.positions.items():
            px = last_prices.get(symbol, pos.entry_price)
            marked += pos.qty * px
        return self.cash + marked

    def realized_pnl(self) -> float:
        return sum(t.pnl for t in self.closed)


@dataclass
class Strategy:
    """Multi-symbol realtime momentum. `symbol` is the primary watch seed (often SPY)."""

    symbol: str = "SPY"
    account: Account = field(default_factory=Account)
    symbols: dict[str, SymbolState] = field(default_factory=lambda: defaultdict(SymbolState))
    last_prices: dict[str, float] = field(default_factory=dict)
    last_bids: dict[str, float | None] = field(default_factory=dict)
    last_asks: dict[str, float | None] = field(default_factory=dict)
    last_price: float | None = None  # feed/equity helper — latest traded mark
    buy_pct_5m: float | None = None
    high_rate_5m: int | None = None
    low_rate_5m: int | None = None
    last_fill_meta: dict[str, Any] = field(default_factory=dict)
    watch_dirty: bool = False
    max_positions: int = field(default_factory=_default_max_positions)
    cash_fraction: float = field(default_factory=_default_cash_fraction)
    trail_activation_pct: float = field(default_factory=_default_trail_arm)
    trail_pct: float = field(default_factory=_default_trail_pct)
    trail_min_hold_sec: float = field(default_factory=_default_trail_min_hold)
    entry_cutoff_et: tuple[int, int] = field(default_factory=_default_entry_cutoff_et)

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

    def watch_symbols(self) -> list[str]:
        """Symbols that need price_update ticks (open positions + seed)."""
        watched = {self.symbol.upper()}
        watched.update(s.upper() for s in self.account.positions)
        return sorted(watched)

    def pnl_metrics(self) -> tuple[float, float, int]:
        realized = self.account.realized_pnl()
        unrealized = 0.0
        for symbol, pos in self.account.positions.items():
            last = self.last_prices.get(symbol, pos.entry_price)
            unrealized += (last - pos.entry_price) * pos.qty
        return realized, unrealized, len(self.account.positions)

    def on_quote(self, symbol: str, ev: dict) -> None:
        bid = ev.get("bid")
        ask = ev.get("ask")
        if bid is not None:
            self.last_bids[symbol] = float(bid)
        if ask is not None:
            self.last_asks[symbol] = float(ask)

    def _record_fill_spread(self, spread: float) -> None:
        self.account.fill_spreads.append(spread)

    def _simulate_fill(
        self, side: str, symbol: str, ref_price: float, qty: float
    ) -> tuple[float, float, float]:
        px, spread = fill_price(
            side,
            last_price=ref_price,
            bid=self.last_bids.get(symbol),
            ask=self.last_asks.get(symbol),
        )
        return px, spread, commission_for_shares(qty)

    def _size_for_entry(self, price: float) -> float:
        if price <= 0 or self.cash_fraction <= 0:
            return 0.0
        budget = self.account.cash * self.cash_fraction
        per_share = price + COMMISSION_PER_SHARE
        if per_share <= 0:
            return 0.0
        return float(int(budget // per_share))

    def _in_reentry_cooldown(self, symbol: str, ts_sec: float) -> bool:
        last = self.symbols[symbol].last_exit_ts_sec
        if last is None:
            return False
        return ts_sec - last < REENTRY_COOLDOWN_SEC

    def _past_entry_cutoff(self, ts_sec: float) -> bool:
        dt = datetime.fromtimestamp(ts_sec, tz=TZ_ET)
        t_minutes = dt.hour * 60 + dt.minute
        cutoff_minutes = self.entry_cutoff_et[0] * 60 + self.entry_cutoff_et[1]
        close_minutes = SESSION_CLOSE_ET[0] * 60 + SESSION_CLOSE_ET[1]
        open_minutes = SESSION_OPEN_ET[0] * 60 + SESSION_OPEN_ET[1]
        if t_minutes < open_minutes or t_minutes >= close_minutes:
            return False
        return t_minutes >= cutoff_minutes

    def _update_market_from_tape(self, ev: dict) -> None:
        buy = ev.get("buy_pct_5m")
        if buy is not None:
            self.buy_pct_5m = float(buy)
        hi = ev.get("market_high_rate_5m")
        lo = ev.get("market_low_rate_5m")
        if hi is not None:
            self.high_rate_5m = int(hi)
        if lo is not None:
            self.low_rate_5m = int(lo)

    def handle_event(self, ev: dict, ts_sec: float) -> list[dict]:
        """Dispatch one TAPE_EVENT. Returns ALGO_* ingress frames."""
        kind = ev.get("event") or ""
        out: list[dict] = []

        if kind == "market_summary" or ev.get("buy_pct_5m") is not None:
            self._update_market_from_tape(ev)
            if self.breadth_broken() and self.account.positions:
                out.extend(self._flatten_all(ts_sec, _iso(ts_sec), "breadth_kill_switch"))
            if kind == "market_summary":
                return out

        symbol = (ev.get("symbol") or "").strip().upper()
        if not symbol:
            return out

        self.on_quote(symbol, ev)
        price = ev.get("last_price")
        if price is not None:
            px = float(price)
            self.last_prices[symbol] = px
            self.last_price = px

        now = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
        st = self.symbols[symbol]

        if "high" in kind:
            if ev.get("high_count") is not None:
                st.cum_high = int(ev["high_count"])
            st.recent.append((now, "high"))
        if "low" in kind:
            if ev.get("low_count") is not None:
                st.cum_low = int(ev["low_count"])
            st.recent.append((now, "low"))

        # Tick risk management for any open name (price_update / high / low)
        if symbol in self.account.positions and price is not None:
            out.extend(self._check_tick_exits(symbol, ts_sec, float(price)))
            if symbol not in self.account.positions:
                return out

        # Stacking-lows exit before considering a fresh entry on the same tick
        if symbol in self.account.positions and "low" in kind:
            _, recent_low = st.recent_counts(now)
            if recent_low >= RECENT_LOW_EXIT_THRESHOLD and price is not None:
                out.extend(
                    self._exit(symbol, float(price), ts_sec, _iso(ts_sec), "stacking_lows")
                )
            return out

        if (
            symbol not in self.account.positions
            and "high" in kind
            and bool(ev.get("volume_spike"))
            and price is not None
            and self.breadth_ok()
            and self.qualifies(symbol, now)
            and not self._in_reentry_cooldown(symbol, ts_sec)
            and not self._past_entry_cutoff(ts_sec)
            and len(self.account.positions) < self.max_positions
        ):
            recent_high, _ = st.recent_counts(now)
            out.extend(
                self._enter(
                    symbol,
                    float(price),
                    ts_sec,
                    _iso(ts_sec),
                    reason="momentum_new_high",
                    cum_high=st.cum_high,
                    recent_high=recent_high,
                )
            )

        return out

    def _check_tick_exits(self, symbol: str, ts_sec: float, price: float) -> list[dict]:
        pos = self.account.positions.get(symbol)
        if pos is None:
            return []
        out = self._check_stop(symbol, ts_sec, price)
        if out:
            return out
        held = ts_sec - pos.entry_ts_sec
        if held >= self.trail_min_hold_sec:
            return self._check_trail(symbol, ts_sec, price)
        return []

    def _check_stop(self, symbol: str, ts_sec: float, price: float) -> list[dict]:
        pos = self.account.positions.get(symbol)
        if pos is None:
            return []
        if pos.qty > 0 and price <= pos.entry_price * (1.0 - STOP_PCT):
            return self._exit(symbol, price, ts_sec, _iso(ts_sec), "stop_loss")
        return []

    def _check_trail(self, symbol: str, ts_sec: float, price: float) -> list[dict]:
        pos = self.account.positions.get(symbol)
        if pos is None:
            return []
        if pos.trail_extreme is None:
            pos.trail_extreme = pos.entry_price
        if pos.qty > 0:
            pos.trail_extreme = max(pos.trail_extreme, price)
            peak = pos.trail_extreme
            gain = (peak - pos.entry_price) / pos.entry_price
            if gain >= self.trail_activation_pct and price <= peak * (1.0 - self.trail_pct):
                return self._exit(symbol, price, ts_sec, _iso(ts_sec), "trailing_stop")
        return []

    def _fill_meta(self, symbol: str, **extra: Any) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "symbol": symbol,
            "buy_pct_5m": self.buy_pct_5m,
            "high_rate_5m": self.high_rate_5m,
            "low_rate_5m": self.low_rate_5m,
            "open_positions": len(self.account.positions),
        }
        meta.update(extra)
        return meta

    def _enter(
        self,
        symbol: str,
        price: float,
        ts_sec: float,
        ts: str,
        *,
        reason: str,
        cum_high: int | None = None,
        recent_high: int | None = None,
    ) -> list[dict]:
        qty = self._size_for_entry(price)
        if qty <= 0:
            return []
        fill_px, spread, comm = self._simulate_fill("BUY", symbol, price, qty)
        cost = qty * fill_px + comm
        if cost > self.account.cash:
            return []
        self.account.cash -= cost
        self.account.total_commission += comm
        self._record_fill_spread(spread)
        meta = self._fill_meta(
            symbol,
            cum_high=cum_high,
            recent_high=recent_high,
            reason=reason,
        )
        self.last_fill_meta = meta
        self.account.positions[symbol] = Position(
            symbol=symbol,
            qty=qty,
            entry_price=fill_px,
            entry_ts=ts,
            entry_ts_sec=ts_sec,
            entry_spread=spread,
            entry_commission=comm,
            trail_extreme=fill_px,
            entry_reason=reason,
            entry_cum_high=cum_high,
            entry_recent_high=recent_high,
        )
        self.watch_dirty = True
        return _trade_events(symbol, "BUY", qty, fill_px, ts, reason)

    def _exit(
        self,
        symbol: str,
        price: float,
        ts_sec: float,
        ts: str,
        reason: str,
    ) -> list[dict]:
        pos = self.account.positions.get(symbol)
        if pos is None:
            return []
        fill_px, spread, comm = self._simulate_fill("SELL", symbol, price, pos.qty)
        self.account.cash += pos.qty * fill_px - comm
        self.account.total_commission += comm
        self._record_fill_spread(spread)
        trade_comm = pos.entry_commission + comm
        pnl = (fill_px - pos.entry_price) * pos.qty - trade_comm
        meta = self._fill_meta(symbol, reason=reason, trade_pnl=pnl)
        self.last_fill_meta = meta
        self.account.closed.append(
            ClosedTrade(
                symbol=symbol,
                entry_ts=pos.entry_ts,
                exit_ts=ts,
                qty=pos.qty,
                entry_price=pos.entry_price,
                exit_price=fill_px,
                pnl=pnl,
                reason=reason,
                entry_spread=pos.entry_spread,
                exit_spread=spread,
                commission=trade_comm,
                entry_reason=pos.entry_reason,
                entry_cum_high=pos.entry_cum_high,
                entry_recent_high=pos.entry_recent_high,
            )
        )
        del self.account.positions[symbol]
        self.symbols[symbol].last_exit_ts_sec = ts_sec
        self.watch_dirty = True
        return _trade_events(symbol, "SELL", abs(pos.qty), fill_px, ts, reason)

    def _flatten_all(self, ts_sec: float, ts: str, reason: str) -> list[dict]:
        out: list[dict] = []
        for symbol in list(self.account.positions):
            ref = self.last_prices.get(symbol) or self.account.positions[symbol].entry_price
            out.extend(self._exit(symbol, ref, ts_sec, ts, reason))
        return out


def _trade_events(
    symbol: str, side: str, size: float, price: float, ts: str, reason: str
) -> list[dict]:
    return [
        {
            "type": "ALGO_SIGNAL",
            "symbol": symbol,
            "side": side,
            "strength": 0.8,
            "reason": reason,
            "algo_id": ALGO_ID,
            "timestamp": ts,
        },
        {
            "type": "ALGO_ORDER",
            "symbol": symbol,
            "side": side,
            "size": size,
            "price": price,
            "algo_id": ALGO_ID,
            "fill_status": "FILLED",
            "timestamp": ts,
        },
        {
            "type": "ALGO_FILL",
            "symbol": symbol,
            "side": side,
            "size": size,
            "fill_price": price,
            "algo_id": ALGO_ID,
            "timestamp": ts,
        },
    ]
