"""Opening-Range Breakout — the validated edge.

After the first ``or_minutes`` from the 09:30 ET open, the high/low of that window
define an OCO pair: buy-stop at the high (protective stop at the low) and sell-stop
at the low (protective stop at the high). First to trigger wins; the other cancels.
No fixed target by default (exit at the flatten time tested best). One trade/day.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from kestrel.instruments import InstrumentSpec
from kestrel.strategy.base import DayPlan


class ORBStrategy:
    name = "orb"

    def __init__(self, spec: InstrumentSpec, target_R: Optional[float] = None):
        self.spec = spec
        self.session = spec.session
        self.target_R = target_R

    def build_plan(self, day_bars: pd.DataFrame, day) -> Optional[DayPlan]:
        """``day_bars`` are this day's bars from the open through the OR end (ET)."""
        s = self.session
        orb = day_bars[(day_bars["et_min"] >= s.open_m) & (day_bars["et_min"] < s.or_end_m)]
        if len(orb) < 2:
            return None
        hi, lo = float(orb["high"].max()), float(orb["low"].min())
        plan = DayPlan(day=day, or_high=hi, or_low=lo, target_R=self.target_R)
        return plan if plan.valid else None
