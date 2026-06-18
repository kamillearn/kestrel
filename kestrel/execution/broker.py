"""Venue-agnostic broker interface. The runner places ONE OCO bracket per day per
instrument; adapters translate it to native orders."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
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
    def place_oco(self, b: OcoBracket) -> list[str]: ...
    @abstractmethod
    def open_orders(self, instrument: str) -> list[str]: ...
    @abstractmethod
    def cancel_all(self, instrument: str): ...
    @abstractmethod
    def positions(self) -> list[Position]: ...
    @abstractmethod
    def flatten(self, instrument: str): ...
