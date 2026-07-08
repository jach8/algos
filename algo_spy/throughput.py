"""Multi-horizon market throughput (30s / 1m / 5m / 20m) from TAPE_EVENT frames."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal


def _env_flow_mode() -> str:
    raw = os.environ.get("ALGO_SPY_FLOW_MODE", "score").strip().lower()
    return raw if raw in ("off", "score") else "score"


def _env_flow_weight() -> float:
    try:
        return float(os.environ.get("ALGO_SPY_FLOW_WEIGHT", "0.5"))
    except ValueError:
        return 0.5


def _env_flow_edge() -> float:
    try:
        return float(os.environ.get("ALGO_SPY_FLOW_EDGE", "20"))
    except ValueError:
        return 20.0


def _env_flow_min_events() -> int:
    try:
        return int(os.environ.get("ALGO_SPY_FLOW_MIN_EVENTS", "10"))
    except ValueError:
        return 10

WINDOW_ORDER = ("30s", "1m", "5m", "20m")
SHORT_WINDOWS = ("30s", "1m")
MEDIUM_WINDOWS = ("5m", "20m")

# v2 breadth scoring (Phase 0 locked spec)
BREADTH_EDGE = 2
BREADTH_WEIGHTS: dict[str, int] = {"30s": 4, "1m": 3, "5m": 2, "20m": 1}
BREADTH_CAPS: dict[str, int] = {"30s": 40, "1m": 60, "5m": 150, "20m": 400}
T_ENTRY = 10.0
T_WARN = 4.0
T_FLAT = 3.0
# Medium must cross this far against position before breadth_medium_roll can fire.
T_EXIT_MEDIUM = 50.0
ENTRY_DEBOUNCE = 2
EXIT_MEDIUM_CONFIRM = 5

DivergenceState = Literal["aligned", "divergence", "narrowing"]

# v1 exit checks (legacy helpers — not used by v2 strategy)
THROUGHPUT_INVERT_WINDOWS = ("5m", "20m")
THROUGHPUT_INVERT_MIN_WINDOWS = 2
THROUGHPUT_EDGE = BREADTH_EDGE  # alias for strategy.py imports

# TapeEvent fields map to tracker buckets [20m, 5m, 1m, 30s] on the Rust side.
_WINDOW_FIELDS: tuple[tuple[str, str], ...] = (
    ("market_high_rate_30s", "market_low_rate_30s"),
    ("market_high_rate_1m", "market_low_rate_1m"),
    ("market_high_rate_5m", "market_low_rate_5m"),
    ("market_high_rate_20m", "market_low_rate_20m"),
)


@dataclass(frozen=True)
class WindowRates:
    high: int = 0
    low: int = 0


@dataclass(frozen=True)
class BreadthSnapshot:
    """Weighted market breadth score and subscores at a point in time."""

    score: float
    short_breadth: float
    medium_breadth: float
    contributions: dict[str, float]

    def in_flat_zone(self) -> bool:
        return abs(self.score) <= T_FLAT

    def market_short_bearish(self) -> bool:
        return self.short_breadth < -T_WARN

    def market_short_bullish(self) -> bool:
        return self.short_breadth > T_WARN

    def market_medium_bullish(self) -> bool:
        return self.medium_breadth > 0

    def market_medium_bearish(self) -> bool:
        return self.medium_breadth < 0

    def meets_long_entry_score(self) -> bool:
        return self.score >= T_ENTRY

    def meets_short_entry_score(self) -> bool:
        return self.score <= -T_ENTRY

    def format_scores(self) -> str:
        flow = self.contributions.get("flow", 0.0)
        return (
            f"score={self.score:+.0f} short={self.short_breadth:+.0f} "
            f"medium={self.medium_breadth:+.0f} flow={flow:+.0f}"
        )


def window_contribution(high: int, low: int, label: str, edge: int = BREADTH_EDGE) -> float:
    """Signed capped contribution for one horizon."""
    weight = BREADTH_WEIGHTS[label]
    cap = BREADTH_CAPS[label]
    bias = high - low
    if bias > edge:
        return weight * min(bias, cap)
    if bias < -edge:
        return -weight * min(abs(bias), cap)
    return 0.0


def divergence_state(
    snapshot: BreadthSnapshot,
    *,
    spy_structure_up: bool,
) -> DivergenceState:
    """Layer 1b — market short-TF vs SPY structure."""
    if snapshot.market_short_bearish() and spy_structure_up:
        return "divergence"
    if snapshot.market_short_bearish() and not snapshot.market_medium_bullish() and not spy_structure_up:
        return "narrowing"
    return "aligned"


@dataclass
class MarketThroughput:
    """Rolling new-high / new-low event counts per horizon."""

    windows: dict[str, WindowRates] = field(
        default_factory=lambda: {w: WindowRates() for w in WINDOW_ORDER}
    )
    flow_buy_pct: float | None = None
    flow_sell_pct: float | None = None
    flow_events: int = 0
    flow_mode: str = field(default_factory=_env_flow_mode)
    flow_weight: float = field(default_factory=_env_flow_weight)
    flow_edge: float = field(default_factory=_env_flow_edge)
    flow_min_events: int = field(default_factory=_env_flow_min_events)

    def update_from_tape_event(self, ev: dict) -> None:
        """Refresh horizons from TapeEvent rate fields (and roll_summary fallback)."""
        for label, (hk, lk) in zip(WINDOW_ORDER, _WINDOW_FIELDS):
            if hk in ev and lk in ev:
                self.windows[label] = WindowRates(
                    int(ev.get(hk) or 0),
                    int(ev.get(lk) or 0),
                )

        if ev.get("event") != "roll_window_summary":
            return
        summary = ev.get("roll_summary")
        if not isinstance(summary, dict):
            return
        panes = summary.get("panes") or []
        for i, label in enumerate(WINDOW_ORDER):
            hk, lk = _WINDOW_FIELDS[i]
            if hk in ev and lk in ev:
                continue
            if i >= len(panes) or not isinstance(panes[i], dict):
                continue
            pane = panes[i]
            h = sum(int(x.get("count", 0)) for x in (pane.get("highs") or []))
            l = sum(int(x.get("count", 0)) for x in (pane.get("lows") or []))
            self.windows[label] = WindowRates(h, l)

        flow = summary.get("flow_push_pull")
        if isinstance(flow, dict):
            bp = flow.get("buy_pct_5m")
            sp = flow.get("sell_pct_5m")
            self.flow_buy_pct = float(bp) if bp is not None else None
            self.flow_sell_pct = float(sp) if sp is not None else None
            self.flow_events = int(flow.get("flow_events_5m") or 0)

    def _rates(self, label: str) -> WindowRates:
        return self.windows.get(label, WindowRates())

    def contribution(self, label: str, edge: int = BREADTH_EDGE) -> float:
        r = self._rates(label)
        return window_contribution(r.high, r.low, label, edge)

    def flow_contribution(self) -> float:
        """Signed, deadzoned, sample-gated 5m L1 push-pull term ([-100,100] × weight)."""
        if self.flow_mode == "off":
            return 0.0
        if self.flow_buy_pct is None or self.flow_sell_pct is None:
            return 0.0
        if self.flow_events < self.flow_min_events:
            return 0.0
        net = self.flow_buy_pct - self.flow_sell_pct  # signed [-100, 100]
        if abs(net) < self.flow_edge:
            return 0.0
        return self.flow_weight * net

    def breadth_snapshot(self, edge: int = BREADTH_EDGE) -> BreadthSnapshot:
        """v2 weighted breadth score and short/medium subscores."""
        contribs = {w: self.contribution(w, edge) for w in WINDOW_ORDER}
        flow = self.flow_contribution()
        contribs["flow"] = flow
        short = sum(contribs[w] for w in SHORT_WINDOWS)
        medium = sum(contribs[w] for w in MEDIUM_WINDOWS) + flow
        return BreadthSnapshot(
            score=sum(contribs[w] for w in WINDOW_ORDER) + flow,
            short_breadth=short,
            medium_breadth=medium,
            contributions=contribs,
        )

    def backdrop_20m_bearish(self, edge: int = BREADTH_EDGE) -> bool:
        r = self._rates("20m")
        return r.low > r.high + edge

    def backdrop_20m_bullish(self, edge: int = BREADTH_EDGE) -> bool:
        r = self._rates("20m")
        return r.high > r.low + edge

    def allows_long_entry(self, snapshot: BreadthSnapshot | None = None) -> bool:
        """Static long entry gate (debounce applied in strategy)."""
        snap = snapshot or self.breadth_snapshot()
        return (
            snap.meets_long_entry_score()
            and snap.market_medium_bullish()
            and not self.backdrop_20m_bearish()
        )

    def allows_short_entry(
        self,
        snapshot: BreadthSnapshot | None = None,
        *,
        spy_structure_up: bool = False,
    ) -> bool:
        """Static short entry gate; block on divergence when SPY still leading."""
        snap = snapshot or self.breadth_snapshot()
        if spy_structure_up and snap.market_short_bearish() and snap.market_medium_bullish():
            return False
        return (
            snap.meets_short_entry_score()
            and snap.market_medium_bearish()
            and not self.backdrop_20m_bullish()
        )

    # --- v1 legacy (strategy until Phase 3) ---

    def _count_bearish_windows(self, edge: int, windows: tuple[str, ...] = WINDOW_ORDER) -> int:
        return sum(
            1
            for w in windows
            if self._rates(w).low > self._rates(w).high + edge
        )

    def _count_bullish_windows(self, edge: int, windows: tuple[str, ...] = WINDOW_ORDER) -> int:
        return sum(
            1
            for w in windows
            if self._rates(w).high > self._rates(w).low + edge
        )

    def is_bearish_vs_long(self, edge: int) -> bool:
        return (
            self._count_bearish_windows(edge, THROUGHPUT_INVERT_WINDOWS)
            >= THROUGHPUT_INVERT_MIN_WINDOWS
            and self.net_high_low_spread() < 0
        )

    def is_bullish_vs_short(self, edge: int) -> bool:
        return (
            self._count_bullish_windows(edge, THROUGHPUT_INVERT_WINDOWS)
            >= THROUGHPUT_INVERT_MIN_WINDOWS
            and self.net_high_low_spread() > 0
        )

    def windows_confirming_long(self, edge: int) -> int:
        return self._count_bullish_windows(edge)

    def windows_confirming_short(self, edge: int) -> int:
        return self._count_bearish_windows(edge)

    def windows_inverted_vs_long(self, edge: int) -> int:
        return self._count_bearish_windows(edge)

    def windows_inverted_vs_short(self, edge: int) -> int:
        return self._count_bullish_windows(edge)

    def net_high_low_spread(self) -> float:
        return float(
            sum(self._rates(w).high - self._rates(w).low for w in WINDOW_ORDER)
        )

    def format_snapshot(self) -> str:
        parts = []
        for w in WINDOW_ORDER:
            r = self._rates(w)
            parts.append(f"{w} H{r.high}/L{r.low}")
        snap = self.breadth_snapshot()
        return f"{snap.format_scores()} | {' '.join(parts)}"
