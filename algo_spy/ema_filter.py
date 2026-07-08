"""5m bar-close EMA filter — soft score, compression tell, ignition (v2)."""
from __future__ import annotations

from dataclasses import dataclass

T_EMA_SOFT = 1.0


@dataclass(frozen=True)
class EmaFilterSnapshot:
    long_score: float
    short_score: float
    hard_veto_long: bool
    hard_veto_short: bool
    compression_caution: bool
    bearish_ignition: bool
    bullish_ignition: bool
    spread_23: float
    ema3_slope: float

    def allows_long(self) -> bool:
        return not self.hard_veto_long and self.long_score >= T_EMA_SOFT

    def allows_long_reload(self) -> bool:
        return not self.hard_veto_long and self.long_score >= 0

    def allows_short(self) -> bool:
        return not self.hard_veto_short and self.short_score <= -T_EMA_SOFT

    def allows_short_reload(self) -> bool:
        return not self.hard_veto_short and self.short_score <= 0


@dataclass
class EmaFilterTracker:
    """Stateful 5m bar-close EMA geometry (streaks + compression)."""

    ema1_above_ema2_streak: int = 0
    ema1_below_ema2_streak: int = 0
    prev_spread_23: float | None = None
    prev_ema3: float | None = None

    def on_bar_close(
        self,
        *,
        close: float,
        ema1: float,
        ema2: float,
        ema3: float,
    ) -> EmaFilterSnapshot:
        spread_23 = ema2 - ema3
        ema3_slope = 0.0 if self.prev_ema3 is None else ema3 - self.prev_ema3
        spread_narrowing = (
            self.prev_spread_23 is not None and spread_23 < self.prev_spread_23
        )

        if ema1 > ema2:
            self.ema1_above_ema2_streak += 1
            self.ema1_below_ema2_streak = 0
        elif ema1 < ema2:
            self.ema1_below_ema2_streak += 1
            self.ema1_above_ema2_streak = 0
        else:
            self.ema1_above_ema2_streak = 0
            self.ema1_below_ema2_streak = 0

        hard_veto_long = close < ema3
        hard_veto_short = close > ema3

        long_score = self._long_score(
            close=close,
            ema1=ema1,
            ema2=ema2,
            ema3=ema3,
            spread_narrowing=spread_narrowing,
            ema3_rising=ema3_slope > 0,
        )
        short_score = self._short_score(
            close=close,
            ema1=ema1,
            ema2=ema2,
            ema3=ema3,
            spread_narrowing=spread_narrowing,
            ema3_falling=ema3_slope < 0,
        )

        bearish_ignition = self.ema1_below_ema2_streak >= 2 or (
            self.ema1_below_ema2_streak >= 1 and ema1 < ema3
        )
        bullish_ignition = self.ema1_above_ema2_streak >= 2 or (
            self.ema1_above_ema2_streak >= 1 and ema1 > ema3
        )
        compression_caution = (
            spread_narrowing and ema3_slope > 0 and not bearish_ignition
        )

        self.prev_spread_23 = spread_23
        self.prev_ema3 = ema3

        return EmaFilterSnapshot(
            long_score=long_score,
            short_score=short_score,
            hard_veto_long=hard_veto_long,
            hard_veto_short=hard_veto_short,
            compression_caution=compression_caution,
            bearish_ignition=bearish_ignition,
            bullish_ignition=bullish_ignition,
            spread_23=spread_23,
            ema3_slope=ema3_slope,
        )

    def _long_score(
        self,
        *,
        close: float,
        ema1: float,
        ema2: float,
        ema3: float,
        spread_narrowing: bool,
        ema3_rising: bool,
    ) -> float:
        if close < ema3:
            return 0.0
        score = 2.0
        if ema1 > ema2:
            score += 2.0
            if self.ema1_above_ema2_streak >= 2:
                score += 1.0
        elif self.ema1_below_ema2_streak == 1:
            if spread_narrowing and ema3_rising:
                score -= 1.0
        elif self.ema1_below_ema2_streak >= 2 or ema1 < ema3:
            score -= 2.0
        return score

    def _short_score(
        self,
        *,
        close: float,
        ema1: float,
        ema2: float,
        ema3: float,
        spread_narrowing: bool,
        ema3_falling: bool,
    ) -> float:
        if close > ema3:
            return 0.0
        score = -2.0
        if ema1 < ema2:
            score -= 2.0
            if self.ema1_below_ema2_streak >= 2:
                score -= 1.0
        elif self.ema1_above_ema2_streak == 1:
            if spread_narrowing and ema3_falling:
                score += 1.0
        elif self.ema1_above_ema2_streak >= 2 or ema1 > ema3:
            score += 2.0
        return score
