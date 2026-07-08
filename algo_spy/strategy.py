"""SPY v2: breadth-first entries; EMA optional (see ema_mode).

Market throughput (rate bars) leads; entries at 5m bar close when score + subscores align.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from .bars import BAR_5M_SEC, Bar, BarAggregator, EmaStack
from .ema_filter import EmaFilterSnapshot, EmaFilterTracker
from .execution import COMMISSION_PER_SHARE, commission_for_shares, fill_price
from .throughput import (
    ENTRY_DEBOUNCE,
    EXIT_MEDIUM_CONFIRM,
    MarketThroughput,
    T_ENTRY,
    T_EXIT_MEDIUM,
    T_WARN,
    BreadthSnapshot,
    divergence_state,
)

ALGO_ID = "spy_breadth_ema_v2"
MIN_HOLD_SEC = 300.0
REENTRY_COOLDOWN_SEC = 300.0
STOP_PCT = 0.04
# ~0.20% arm — above ~0.17% round-trip commission breakeven at typical size.
DEFAULT_TRAIL_ACTIVATION_PCT = 0.002
DEFAULT_TRAIL_PCT = 0.001  # 0.10% pullback from peak once armed
DEFAULT_TRAIL_MIN_HOLD_SEC = 60.0  # trail can fire after 1m (breadth/EMA still 5m)
TIME_STOP_SEC = 30.0 * 60.0
TIME_STOP_MIN_MOVE_PCT = 0.0015
SPY_NEW_HIGH_WINDOW_SEC = 15.0 * 60.0

TZ_ET = ZoneInfo("America/New_York")
SESSION_CLOSE_ET = (16, 0)  # regular RTH close (ET)
SESSION_OPEN_ET = (9, 30)
DEFAULT_NO_ENTRY_MINUTES_BEFORE_CLOSE = 30

EmaMode = Literal["breadth", "off", "full"]
DEFAULT_EMA_MODE: EmaMode = "breadth"

# Legacy aliases for tests importing old names
THROUGHPUT_EDGE = 2
THROUGHPUT_MIN_WINDOWS = 2


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    raw = raw.split("#", 1)[0].strip()
    return float(raw)


def _default_trail_arm() -> float:
    return _env_float("ALGO_SPY_TRAIL_ARM", DEFAULT_TRAIL_ACTIVATION_PCT)


def _default_trail_pct() -> float:
    return _env_float("ALGO_SPY_TRAIL_PCT", DEFAULT_TRAIL_PCT)


def _default_trail_min_hold() -> float:
    return _env_float("ALGO_SPY_TRAIL_MIN_HOLD_SEC", DEFAULT_TRAIL_MIN_HOLD_SEC)


def _default_entry_cutoff_et() -> tuple[int, int]:
    """Latest ET clock time (hour, minute) for a new 5m-bar entry (default 15:30)."""
    raw = os.environ.get("ALGO_SPY_ENTRY_CUTOFF_ET")
    if raw is not None and raw.strip():
        part = raw.split("#", 1)[0].strip()
        hour_str, minute_str = part.split(":", 1)
        return int(hour_str), int(minute_str)
    mins = int(
        _env_float(
            "ALGO_SPY_NO_ENTRY_MINUTES_BEFORE_CLOSE",
            float(DEFAULT_NO_ENTRY_MINUTES_BEFORE_CLOSE),
        )
    )
    close_h, close_m = SESSION_CLOSE_ET
    total = close_h * 60 + close_m - mins
    return total // 60, total % 60


def _default_ema_mode() -> EmaMode:
    raw = os.environ.get("ALGO_SPY_EMA_MODE", DEFAULT_EMA_MODE)
    if raw is None:
        return DEFAULT_EMA_MODE
    key = raw.split("#", 1)[0].strip().lower()
    if key in ("full", "legacy", "v2"):
        return "full"
    if key in ("off", "none"):
        return "off"
    return "breadth"


def _iso(ts_sec: float) -> str:
    dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


@dataclass
class Position:
    qty: float
    entry_price: float
    entry_ts: str
    entry_spread: float | None = None
    entry_commission: float = 0.0
    entry_score: float | None = None
    entry_short: float | None = None
    entry_medium: float | None = None
    entry_divergence: str | None = None


@dataclass
class ClosedTrade:
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
    entry_score: float | None = None
    entry_short: float | None = None
    entry_medium: float | None = None
    entry_divergence: str | None = None
    exit_score: float | None = None
    exit_short: float | None = None
    exit_medium: float | None = None
    exit_divergence: str | None = None


@dataclass
class Account:
    starting_cash: float = 10_000.0
    cash: float = 10_000.0
    position: Optional[Position] = None
    closed: list[ClosedTrade] = field(default_factory=list)
    total_commission: float = 0.0
    fill_spreads: list[float] = field(default_factory=list)

    def equity(self, last_price: float) -> float:
        if self.position is None:
            return self.cash
        return self.cash + self.position.qty * last_price

    def realized_pnl(self) -> float:
        return sum(t.pnl for t in self.closed)


@dataclass
class Strategy:
    symbol: str = "SPY"
    bars_5m: BarAggregator = field(default_factory=lambda: BarAggregator(period_sec=BAR_5M_SEC))
    ema: EmaStack = field(default_factory=EmaStack)
    ema_filter: EmaFilterTracker = field(default_factory=EmaFilterTracker)
    account: Account = field(default_factory=Account)
    throughput: MarketThroughput = field(default_factory=MarketThroughput)
    last_price: Optional[float] = None
    last_bid: float | None = None
    last_ask: float | None = None
    entry_ts_sec: float | None = None
    last_exit_ts_sec: float | None = None
    entry_long_streak: int = 0
    entry_short_streak: int = 0
    medium_against_streak: int = 0
    last_spy_new_high_ts: float | None = None
    prev_5m_bar: Bar | None = None
    last_breadth: BreadthSnapshot | None = None
    last_ema_filter: EmaFilterSnapshot | None = None
    last_divergence: str = "aligned"
    last_fill_meta: dict[str, Any] = field(default_factory=dict)
    trail_extreme_price: float | None = None
    trail_activation_pct: float = field(default_factory=_default_trail_arm)
    trail_pct: float = field(default_factory=_default_trail_pct)
    trail_min_hold_sec: float = field(default_factory=_default_trail_min_hold)
    ema_mode: EmaMode = field(default_factory=_default_ema_mode)
    entry_cutoff_et: tuple[int, int] = field(default_factory=_default_entry_cutoff_et)

    def on_quote(self, ev: dict) -> None:
        bid = ev.get("bid")
        ask = ev.get("ask")
        if bid is not None:
            self.last_bid = float(bid)
        if ask is not None:
            self.last_ask = float(ask)

    def _record_fill_spread(self, spread: float) -> None:
        self.account.fill_spreads.append(spread)

    def _simulate_fill(self, side: str, ref_price: float, qty: float) -> tuple[float, float, float]:
        px, spread = fill_price(
            side,
            last_price=ref_price,
            bid=self.last_bid,
            ask=self.last_ask,
        )
        return px, spread, commission_for_shares(qty)

    def seed_ema_from_closed_bars(self, bars: list[Bar]) -> int:
        for bar in bars:
            self.ema.update(bar.close)
            if self.prev_5m_bar is not None:
                pass
            self.prev_5m_bar = bar
        if bars:
            self.last_price = bars[-1].close
        return len(bars)

    def on_price(self, ts_sec: float, price: float) -> list[dict]:
        self.last_price = price
        self.bars_5m.on_tick(ts_sec, price)
        out: list[dict] = []

        for bar in self.bars_5m.pop_closed():
            self.ema.update(bar.close)
            out.extend(self._on_5m_bar_close(bar))

        if self.account.position is not None:
            out.extend(self._check_tick_exits(ts_sec, price))

        return out

    def on_tape_market(self, ev: dict, ts_sec: float) -> list[dict]:
        self.throughput.update_from_tape_event(ev)
        snap = self.throughput.breadth_snapshot()
        self.last_breadth = snap

        if ev.get("symbol") == self.symbol and ev.get("event") in (
            "new_high",
            "new_high_and_low",
        ):
            self.last_spy_new_high_ts = ts_sec

        self._update_entry_streaks(snap)

        if self.account.position is None:
            return []
        if self._before_min_hold(ts_sec):
            return []
        return self._check_breadth_exits(ts_sec, snap)

    def _update_entry_streaks(self, snap: BreadthSnapshot) -> None:
        if snap.meets_long_entry_score():
            self.entry_long_streak += 1
        else:
            self.entry_long_streak = 0
        if snap.meets_short_entry_score():
            self.entry_short_streak += 1
        else:
            self.entry_short_streak = 0

    def _spy_structure_up(self, ts_sec: float, close: float) -> bool:
        _, _, ema3 = self.ema.levels()
        if ema3 is None or close <= ema3:
            return False
        if (
            self.last_spy_new_high_ts is not None
            and ts_sec - self.last_spy_new_high_ts <= SPY_NEW_HIGH_WINDOW_SEC
        ):
            return True
        if self.prev_5m_bar is not None and close > self.prev_5m_bar.low:
            return True
        return False

    def _on_5m_bar_close(self, bar: Bar) -> list[dict]:
        if not self.ema.ready():
            self.prev_5m_bar = bar
            return []

        ema1, ema2, ema3 = self.ema.levels()
        assert ema1 is not None and ema2 is not None and ema3 is not None

        ts_sec = bar.bucket_epoch * BAR_5M_SEC + BAR_5M_SEC
        ts = _iso(ts_sec)
        ema_snap = self.ema_filter.on_bar_close(
            close=bar.close,
            ema1=ema1,
            ema2=ema2,
            ema3=ema3,
        )
        self.last_ema_filter = ema_snap
        breadth = self.last_breadth or self.throughput.breadth_snapshot()
        self.last_divergence = divergence_state(
            breadth,
            spy_structure_up=self._spy_structure_up(ts_sec, bar.close),
        )

        out: list[dict] = []
        pos = self.account.position
        if pos is not None:
            if self.ema_mode == "full":
                out.extend(self._check_ema_structural_exit(bar, ts_sec, ts, ema_snap))
        else:
            out.extend(self._try_entry_at_bar_close(bar, ts_sec, ts, breadth, ema_snap))

        self.prev_5m_bar = bar
        return out

    def _try_entry_at_bar_close(
        self,
        bar: Bar,
        ts_sec: float,
        ts: str,
        breadth: BreadthSnapshot,
        ema_snap: EmaFilterSnapshot,
    ) -> list[dict]:
        if self._in_reentry_cooldown(ts_sec):
            return []
        if self._past_entry_cutoff(ts_sec):
            return []

        if self.ema_mode == "full":
            return self._try_entry_full_ema(bar, ts_sec, ts, breadth, ema_snap)
        return self._try_entry_breadth_primary(bar, ts_sec, ts, breadth, ema_snap)

    def _try_entry_breadth_primary(
        self,
        bar: Bar,
        ts_sec: float,
        ts: str,
        breadth: BreadthSnapshot,
        ema_snap: EmaFilterSnapshot,
    ) -> list[dict]:
        spy_up = self._spy_structure_up(ts_sec, bar.close)

        if (
            self.entry_long_streak >= ENTRY_DEBOUNCE
            and self.throughput.allows_long_entry(breadth)
            and self._breadth_long_aligned(breadth)
            and self.last_divergence != "divergence"
            and self._ema_hard_allows_long(ema_snap)
        ):
            qty = self._size_for_entry(bar.close)
            if qty > 0:
                return self._enter(qty, bar.close, ts_sec, ts, "breadth_long")

        if (
            self.entry_short_streak >= ENTRY_DEBOUNCE
            and self.throughput.allows_short_entry(breadth, spy_structure_up=spy_up)
            and self._breadth_short_aligned(breadth)
            and self._ema_hard_allows_short(ema_snap)
        ):
            qty = self._size_for_entry(bar.close)
            if qty > 0:
                return self._enter(-qty, bar.close, ts_sec, ts, "breadth_short")

        return []

    def _try_entry_full_ema(
        self,
        bar: Bar,
        ts_sec: float,
        ts: str,
        breadth: BreadthSnapshot,
        ema_snap: EmaFilterSnapshot,
    ) -> list[dict]:
        spy_up = self._spy_structure_up(ts_sec, bar.close)

        if (
            self.entry_long_streak >= ENTRY_DEBOUNCE
            and self.throughput.allows_long_entry(breadth)
        ):
            if ema_snap.allows_long() and not ema_snap.compression_caution:
                qty = self._size_for_entry(bar.close)
                if qty > 0:
                    return self._enter(
                        qty, bar.close, ts_sec, ts, "breadth_long+ema_confirm"
                    )
            elif ema_snap.allows_long_reload():
                qty = self._size_for_entry(bar.close)
                if qty > 0:
                    return self._enter(
                        qty, bar.close, ts_sec, ts, "breadth_long_reload"
                    )

        if (
            self.entry_short_streak >= ENTRY_DEBOUNCE
            and self.throughput.allows_short_entry(breadth, spy_structure_up=spy_up)
        ):
            if ema_snap.allows_short():
                qty = self._size_for_entry(bar.close)
                if qty > 0:
                    return self._enter(
                        -qty, bar.close, ts_sec, ts, "breadth_short+ema_confirm"
                    )
            elif ema_snap.allows_short_reload():
                qty = self._size_for_entry(bar.close)
                if qty > 0:
                    return self._enter(
                        -qty, bar.close, ts_sec, ts, "breadth_short_reload"
                    )

        return []

    def _breadth_long_aligned(self, breadth: BreadthSnapshot) -> bool:
        """Short and medium subscores agree with bullish total score."""
        return breadth.short_breadth >= 0 and breadth.medium_breadth > 0

    def _breadth_short_aligned(self, breadth: BreadthSnapshot) -> bool:
        return breadth.short_breadth <= 0 and breadth.medium_breadth < 0

    def _breadth_still_supports_position(
        self, breadth: BreadthSnapshot, qty: float
    ) -> bool:
        """Breadth still aligned with open position — skip mechanical time_stop."""
        if qty > 0:
            # Hold through short precursor dips while medium backdrop stays bullish.
            return (
                self.throughput.allows_long_entry(breadth)
                and breadth.medium_breadth > 0
            )
        if qty < 0:
            return (
                breadth.score <= -T_ENTRY and self._breadth_short_aligned(breadth)
            )
        return False

    def _past_entry_cutoff(self, ts_sec: float) -> bool:
        """Block new entries in the last N minutes of regular session (default 15:30–16:00 ET)."""
        dt = datetime.fromtimestamp(ts_sec, tz=TZ_ET)
        t_minutes = dt.hour * 60 + dt.minute
        cutoff_minutes = self.entry_cutoff_et[0] * 60 + self.entry_cutoff_et[1]
        close_minutes = SESSION_CLOSE_ET[0] * 60 + SESSION_CLOSE_ET[1]
        open_minutes = SESSION_OPEN_ET[0] * 60 + SESSION_OPEN_ET[1]
        if t_minutes < open_minutes or t_minutes >= close_minutes:
            return False
        return t_minutes >= cutoff_minutes

    def _ema_hard_allows_long(self, ema_snap: EmaFilterSnapshot) -> bool:
        if self.ema_mode == "off":
            return True
        return not ema_snap.hard_veto_long

    def _ema_hard_allows_short(self, ema_snap: EmaFilterSnapshot) -> bool:
        if self.ema_mode == "off":
            return True
        return not ema_snap.hard_veto_short

    def _check_ema_structural_exit(
        self,
        bar: Bar,
        ts_sec: float,
        ts: str,
        ema_snap: EmaFilterSnapshot,
    ) -> list[dict]:
        pos = self.account.position
        if pos is None:
            return []
        if self._before_min_hold(ts_sec):
            return []

        ema1, ema2, ema3 = self.ema.levels()
        assert ema1 is not None and ema2 is not None and ema3 is not None

        if pos.qty > 0:
            if bar.close < ema2 or ema_snap.bearish_ignition:
                return self._exit(bar.close, ts_sec, ts, "ema_structural")
        elif pos.qty < 0:
            if bar.close > ema2 or ema_snap.bullish_ignition:
                return self._exit(bar.close, ts_sec, ts, "ema_structural")
        return []

    def _check_breadth_exits(self, ts_sec: float, snap: BreadthSnapshot) -> list[dict]:
        pos = self.account.position
        if pos is None or self.last_price is None:
            return []

        spy_up = self._spy_structure_up(ts_sec, self.last_price)
        div = divergence_state(snap, spy_structure_up=spy_up)
        self.last_divergence = div

        against = (
            snap.medium_breadth < -T_EXIT_MEDIUM
            if pos.qty > 0
            else snap.medium_breadth > T_EXIT_MEDIUM
        )
        if against:
            self.medium_against_streak += 1
        else:
            self.medium_against_streak = 0

        if self.medium_against_streak >= EXIT_MEDIUM_CONFIRM:
            if pos.qty > 0 and div != "divergence":
                return self._exit(self.last_price, ts_sec, _iso(ts_sec), "breadth_medium_roll")
            if pos.qty < 0:
                return self._exit(self.last_price, ts_sec, _iso(ts_sec), "breadth_medium_roll")

        if pos.qty > 0 and snap.market_short_bearish():
            if div == "divergence":
                return []
            if self.ema_mode == "full":
                _, ema2, _ = self.ema.levels()
                if ema2 is not None and self.last_price < ema2:
                    return self._exit(
                        self.last_price, ts_sec, _iso(ts_sec), "breadth_short_warning"
                    )
            elif snap.medium_breadth <= -T_WARN:
                return self._exit(
                    self.last_price, ts_sec, _iso(ts_sec), "breadth_short_warning"
                )

        return []

    def _check_tick_exits(self, ts_sec: float, price: float) -> list[dict]:
        out = self._check_stop(ts_sec, price)
        if out:
            return out
        if not self._before_trail_min_hold(ts_sec):
            out = self._check_trail(ts_sec, price)
            if out:
                return out
        if self._before_min_hold(ts_sec):
            return []
        return self._check_time_stop(ts_sec, price)

    def _check_stop(self, ts_sec: float, price: float) -> list[dict]:
        pos = self.account.position
        if pos is None:
            return []
        if pos.qty > 0 and price <= pos.entry_price * (1.0 - STOP_PCT):
            return self._exit(price, ts_sec, _iso(ts_sec), "stop_loss")
        if pos.qty < 0 and price >= pos.entry_price * (1.0 + STOP_PCT):
            return self._exit(price, ts_sec, _iso(ts_sec), "stop_loss")
        return []

    def _check_trail(self, ts_sec: float, price: float) -> list[dict]:
        pos = self.account.position
        if pos is None:
            return []

        if self.trail_extreme_price is None:
            self.trail_extreme_price = pos.entry_price

        if pos.qty > 0:
            self.trail_extreme_price = max(self.trail_extreme_price, price)
            peak = self.trail_extreme_price
            gain = (peak - pos.entry_price) / pos.entry_price
            if gain >= self.trail_activation_pct and price <= peak * (1.0 - self.trail_pct):
                return self._exit(price, ts_sec, _iso(ts_sec), "trailing_stop")
        elif pos.qty < 0:
            self.trail_extreme_price = min(self.trail_extreme_price, price)
            trough = self.trail_extreme_price
            gain = (pos.entry_price - trough) / pos.entry_price
            if gain >= self.trail_activation_pct and price >= trough * (1.0 + self.trail_pct):
                return self._exit(price, ts_sec, _iso(ts_sec), "trailing_stop")
        return []

    def _check_time_stop(self, ts_sec: float, price: float) -> list[dict]:
        pos = self.account.position
        if pos is None or self.entry_ts_sec is None:
            return []
        if ts_sec - self.entry_ts_sec < TIME_STOP_SEC:
            return []
        move = abs(price - pos.entry_price) / pos.entry_price
        if move < TIME_STOP_MIN_MOVE_PCT:
            breadth = self.last_breadth or self.throughput.breadth_snapshot()
            if self._breadth_still_supports_position(breadth, pos.qty):
                return []
            return self._exit(price, ts_sec, _iso(ts_sec), "time_stop")
        return []

    def _in_reentry_cooldown(self, ts_sec: float) -> bool:
        if self.last_exit_ts_sec is None:
            return False
        return ts_sec - self.last_exit_ts_sec < REENTRY_COOLDOWN_SEC

    def _before_min_hold(self, ts_sec: float) -> bool:
        if self.entry_ts_sec is None:
            return False
        return ts_sec - self.entry_ts_sec < MIN_HOLD_SEC

    def _before_trail_min_hold(self, ts_sec: float) -> bool:
        if self.entry_ts_sec is None:
            return False
        return ts_sec - self.entry_ts_sec < self.trail_min_hold_sec

    def debug_status(self) -> str:
        pos = "flat"
        if self.account.position is not None:
            pos = "long" if self.account.position.qty > 0 else "short"
        ema1, ema2, ema3 = self.ema.levels()
        breadth = self.last_breadth or self.throughput.breadth_snapshot()
        ema_f = self.last_ema_filter
        ema_bit = ""
        if ema_f is not None:
            ema_bit = f" emaL={ema_f.long_score:+.0f} emaS={ema_f.short_score:+.0f}"
        return (
            f"pos={pos} ema_mode={self.ema_mode} div={self.last_divergence} "
            f"{breadth.format_scores()} entry_streak={self.entry_long_streak}/"
            f"{self.entry_short_streak} need>={ENTRY_DEBOUNCE} "
            f"px={self.last_price} e1={ema1} e2={ema2} e3={ema3}{ema_bit} | "
            f"{self.throughput.format_snapshot()}"
        )

    def _size_for_entry(self, price: float) -> float:
        if price <= 0:
            return 0.0
        per_share = price + COMMISSION_PER_SHARE
        return float(int(self.account.cash // per_share))

    def _breadth_fields(self) -> dict[str, Any]:
        breadth = self.last_breadth or self.throughput.breadth_snapshot()
        return {
            "score": breadth.score,
            "short": breadth.short_breadth,
            "medium": breadth.medium_breadth,
            "divergence": self.last_divergence,
        }

    def _fill_meta(self) -> dict[str, Any]:
        b = self._breadth_fields()
        meta: dict[str, Any] = {
            "breadth_score": b["score"],
            "short_breadth": b["short"],
            "medium_breadth": b["medium"],
            "divergence": b["divergence"],
        }
        if self.last_ema_filter is not None:
            meta["ema_long_score"] = self.last_ema_filter.long_score
            meta["ema_short_score"] = self.last_ema_filter.short_score
        return meta

    def _enter(self, qty: float, price: float, ts_sec: float, ts: str, reason: str) -> list[dict]:
        if qty == 0:
            return []
        side = "BUY" if qty > 0 else "SELL"
        fill_px, spread, comm = self._simulate_fill(side, price, qty)
        if qty > 0:
            cost = qty * fill_px + comm
            if cost > self.account.cash:
                return []
            self.account.cash -= cost
        else:
            proceeds = (-qty) * fill_px - comm
            self.account.cash += proceeds
        self.account.total_commission += comm
        self._record_fill_spread(spread)
        meta = self._fill_meta()
        self.last_fill_meta = meta
        self.account.position = Position(
            qty=qty,
            entry_price=fill_px,
            entry_ts=ts,
            entry_spread=spread,
            entry_commission=comm,
            entry_score=meta.get("breadth_score"),
            entry_short=meta.get("short_breadth"),
            entry_medium=meta.get("medium_breadth"),
            entry_divergence=meta.get("divergence"),
        )
        self.entry_ts_sec = ts_sec
        self.medium_against_streak = 0
        self.trail_extreme_price = fill_px
        return _trade_events(self.symbol, side, abs(qty), fill_px, ts, reason)

    def _exit(self, price: float, ts_sec: float, ts: str, reason: str) -> list[dict]:
        pos = self.account.position
        if pos is None:
            return []
        side = "SELL" if pos.qty > 0 else "BUY"
        fill_px, spread, comm = self._simulate_fill(side, price, pos.qty)
        if pos.qty > 0:
            self.account.cash += pos.qty * fill_px - comm
        else:
            self.account.cash -= (-pos.qty) * fill_px + comm
        self.account.total_commission += comm
        self._record_fill_spread(spread)
        trade_comm = pos.entry_commission + comm
        pnl = (fill_px - pos.entry_price) * pos.qty - trade_comm
        meta = self._fill_meta()
        self.last_fill_meta = meta
        self.account.closed.append(
            ClosedTrade(
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
        self.account.position = None
        self.entry_ts_sec = None
        self.last_exit_ts_sec = ts_sec
        self.medium_against_streak = 0
        self.trail_extreme_price = None
        return _trade_events(self.symbol, side, abs(pos.qty), fill_px, ts, reason)


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
