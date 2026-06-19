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
    timezone: str = "America/New_York"  # Dynamically absorbs DST shifts per region
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


# --- REGIONAL SESSIONS ---

# US Markets (NQ, MNQ, ES, MES, RTY, M2K, SPY)
US_EQUITY = Session()

# Eurozone / Frankfurt Open (DAX, ESTX50)
EU_EQUITY = Session(
    timezone="Europe/Berlin",
    open=time(9, 0),
    close=time(17, 30),     # Cash close
    or_minutes=30,
    flatten=time(17, 25)
)

# London Open (UK FTSE 100)
UK_EQUITY = Session(
    timezone="Europe/London",
    open=time(8, 0),
    close=time(16, 30),     # Cash close
    or_minutes=30,
    flatten=time(16, 25)
)

# Tokyo Open (Nikkei 225)
ASIA_EQUITY = Session(
    timezone="Asia/Tokyo",
    open=time(9, 0),
    close=time(15, 0),      # Cash close
    or_minutes=30,
    flatten=time(14, 55)
)

# Hong Kong Open (Hang Seng Index)
HK_EQUITY = Session(
    timezone="Asia/Hong_Kong",
    open=time(9, 15),       # Dynamic cash open post-auction
    close=time(16, 0),      # Cash close
    or_minutes=30,
    flatten=time(15, 55)
)

# Sydney Open (ASX SPI 200)
AU_EQUITY = Session(
    timezone="Australia/Sydney",
    open=time(9, 50),       # Sequential component roll open
    close=time(16, 30),     # Cash close
    or_minutes=30,
    flatten=time(16, 25)
)


def to_eastern(df: pd.DataFrame, time_col: str | None = "time", source_tz: str = "UTC") -> pd.DataFrame:
    """Converts UTC timestamps to the target market's local timezone."""
    out = df.copy()
    if time_col is not None and time_col in out.columns:
        idx = pd.DatetimeIndex(pd.to_datetime(out[time_col]))
        out = out.drop(columns=[time_col])
    else:
        # THE CRASH FIX:
        idx = pd.DatetimeIndex(pd.to_datetime(out.index))
        
    if idx.tz is None:
        idx = idx.tz_localize(source_tz)
        
    out.index = idx.tz_convert(ET)
    out = out.sort_index()
    
    out["etdate"] = out.index.date
    out["et_min"] = out.index.hour * 60 + out.index.minute
    return out