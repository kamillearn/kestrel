"""Strategy contract. A strategy inspects the opening range and emits a DayPlan:
the resting orders to place for the day. Backtester and live runner both consume
the same plan, so simulated and real execution share one source of truth."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class DayPlan:
    """The day's resting-order plan (an OCO pair of stop-entries with protective stops)."""
    day: date
    or_high: float
    or_low: float
    target_R: Optional[float] = None

    @property
    def risk(self) -> float:
        return self.or_high - self.or_low

    @property
    def valid(self) -> bool:
        return self.risk > 0

    # entry/stop levels for each side
    @property
    def long_entry(self) -> float: return self.or_high
    @property
    def long_stop(self) -> float: return self.or_low
    @property
    def short_entry(self) -> float: return self.or_low
    @property
    def short_stop(self) -> float: return self.or_high

    def long_target(self) -> Optional[float]:
        return self.or_high + self.target_R * self.risk if self.target_R else None

    def short_target(self) -> Optional[float]:
        return self.or_low - self.target_R * self.risk if self.target_R else None
