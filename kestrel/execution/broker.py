"""Venue-agnostic broker interface. The runner places ONE OCO bracket per day per
instrument; adapters translate it to native orders."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd


@dataclass
class OcoBracket:
    """Daybreak's day order: two stop-entries, each with a protective stop and
    optional target; first entry to fill cancels the other (OCO)."""
    instrument: str
    qty: float
    long_entry: float
    long_stop: float
    short_entry: float
    short_stop: float
    long_target: Optional[float] = None
    short_target: Optional[float] = None
    tag: str = "daybreak"
    # Sides the adapter should actually place. The trend filter narrows this to
    # one side ("long",) or ("short",); default places the full two-sided OCO.
    allowed_sides: tuple = ("long", "short")


@dataclass
class Fill:
    instrument: str
    side: str
    qty: float
    price: float


@dataclass
class Position:
    instrument: str
    side: str
    qty: float
    avg_price: float


class OrderKind(str, Enum):
    """Classifies a resting order so the manager can act on the right leg."""
    ENTRY = "entry"
    STOP = "stop"
    TARGET = "target"


@dataclass
class WorkingOrder:
    """A resting (un-filled) order, classified by kind/side so the live manager
    can tell un-triggered entries apart from the protective stop."""
    id: str
    instrument: str
    kind: OrderKind
    side: str          # the position side this leg belongs to: "long" | "short"
    price: float       # stop-trigger or limit price
    qty: float


@dataclass
class BracketHandle:
    """What ``place_oco`` returns: the broker order id for every leg, keyed by
    side, so break-even can locate the stop it owns and the time-decay cancel can
    drop only the un-triggered entries."""
    instrument: str
    long_entry_id: Optional[str] = None
    short_entry_id: Optional[str] = None
    long_stop_id: Optional[str] = None
    short_stop_id: Optional[str] = None
    long_target_id: Optional[str] = None
    short_target_id: Optional[str] = None

    @property
    def entry_ids(self) -> list[str]:
        return [i for i in (self.long_entry_id, self.short_entry_id) if i]

    @property
    def stop_ids(self) -> list[str]:
        return [i for i in (self.long_stop_id, self.short_stop_id) if i]

    def stop_id_for(self, side: str) -> Optional[str]:
        return self.long_stop_id if side == "long" else self.short_stop_id

    def entry_id_for(self, side: str) -> Optional[str]:
        return self.long_entry_id if side == "long" else self.short_entry_id


class Broker(ABC):
    @abstractmethod
    def connect(self): ...
    @abstractmethod
    def disconnect(self): ...
    @abstractmethod
    def equity(self) -> float: ...
    @abstractmethod
    def recent_bars(self, instrument: str, count: int) -> pd.DataFrame: ...
    @abstractmethod
    def place_oco(self, b: OcoBracket) -> "BracketHandle": ...
    @abstractmethod
    def open_orders(self, instrument: str) -> list[str]: ...
    @abstractmethod
    def cancel_all(self, instrument: str): ...
    @abstractmethod
    def positions(self) -> list[Position]: ...
    @abstractmethod
    def flatten(self, instrument: str): ...

    # ---- adaptive-management capabilities (concrete defaults; real adapters
    #      override). Kept non-abstract so existing adapters keep instantiating
    #      while they are upgraded one at a time. ----

    def cancel_order(self, instrument: str, order_id: str) -> None:
        """Cancel ONE resting order, leaving its siblings untouched. The
        time-decay cancel uses this to drop only the un-triggered entries; unlike
        ``cancel_all`` it never strips a live protective stop."""
        raise NotImplementedError

    def modify_stop(self, instrument: str, stop_order_id: str,
                    new_stop_price: float) -> str:
        """Move a protective stop to ``new_stop_price``. Returns the id of the
        active stop afterwards (same id if modified in place, a new id if the
        adapter does cancel+replace). MUST NOT leave the position unprotected —
        prefer modify-in-place. Core primitive for break-even."""
        raise NotImplementedError

    def working_orders(self, instrument: str) -> list["WorkingOrder"]:
        """Resting orders classified by kind/side/price."""
        raise NotImplementedError

    def last_price(self, instrument: str) -> float:
        """Latest price for the 1R-in-profit check. Default = last close of
        ``recent_bars``; adapters may override with a live quote/tick."""
        df = self.recent_bars(instrument, count=2)
        return float(df["close"].iloc[-1]) if len(df) else float("nan")

    def recent_daily_closes(self, instrument: str, n: int):
        """Last ~n completed daily closes (oldest first) for the trend filter.
        Adapters must override with a real daily-bar request — minute bars don't
        reach far enough back."""
        raise NotImplementedError
