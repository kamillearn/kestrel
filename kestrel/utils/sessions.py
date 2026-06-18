"""Session + timezone helpers. Internal logic is US/Eastern so the 09:30 open
is fixed regardless of DST. Source data is treated as UTC unless told otherwise."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Session:
    open: time = time(9, 30)
    close: time = time(16, 0)
    or_minutes: int = 30           # opening-range length
    flatten: time = time(15, 55)   # square off before the close

    def m(self, t: time) -> int:
        return t.hour * 60 + t.minute

    @property
    def open_m(self) -> int: return self.m(self.open)
    @property
    def or_end_m(self) -> int: return self.open_m + self.or_minutes
    @property
    def close_m(self) -> int: return self.m(self.close)
    @property
    def flatten_m(self) -> int: return self.m(self.flatten)


US_EQUITY = Session()


def to_eastern(df: pd.DataFrame, time_col: str | None = "time", source_tz: str = "UTC") -> pd.DataFrame:
    out = df.copy()
    if time_col is not None and time_col in out.columns:
        idx = pd.DatetimeIndex(pd.to_datetime(out[time_col]))
        out = out.drop(columns=[time_col])
    else:
        idx = pd.DatetimeIndex(pd.to_datetime(out.index))
    if idx.tz is None:
        idx = idx.tz_localize(source_tz)
    out.index = idx.tz_convert(ET)
    out = out.sort_index()
    out["etdate"] = out.index.date
    out["et_min"] = out.index.hour * 60 + out.index.minute
    return out
