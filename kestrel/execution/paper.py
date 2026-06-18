"""Paper broker: simulates OCO fills against a replayed bar feed. Used for
integration tests and offline dry-runs. For live paper trading use the real
broker in a practice/paper account, or the runner's --dry-run (logs, no send)."""
from __future__ import annotations

import pandas as pd

from kestrel.execution.broker import Broker, OcoBracket, Position
from kestrel.instruments import InstrumentSpec


class PaperBroker(Broker):
    def __init__(self, feeds: dict[str, pd.DataFrame], specs: dict[str, InstrumentSpec],
                 start_equity: float = 10000.0):
        self.feeds = feeds                 # instrument -> ET-indexed bars
        self.specs = specs
        self._cursor = {k: 0 for k in feeds}
        self._equity = start_equity
        self._orders: dict[str, OcoBracket] = {}
        self._pos: dict[str, Position] = {}

    def connect(self): pass
    def disconnect(self): pass
    def equity(self): return self._equity

    def advance(self, instrument: str, n: int = 1):
        self._cursor[instrument] = min(self._cursor[instrument] + n, len(self.feeds[instrument]))

    def recent_bars(self, instrument, count=800):
        c = self._cursor[instrument]
        return self.feeds[instrument].iloc[max(0, c - count):c]

    def place_oco(self, b: OcoBracket):
        self._orders[b.instrument] = b
        return [f"{b.instrument}-L", f"{b.instrument}-S"]

    def open_orders(self, instrument):
        return [] if instrument not in self._orders else [f"{instrument}-pending"]

    def cancel_all(self, instrument):
        self._orders.pop(instrument, None)

    def positions(self):
        return list(self._pos.values())

    def flatten(self, instrument):
        self._pos.pop(instrument, None)

    # test helper: settle a closed trade's pnl in points
    def settle(self, instrument, pnl_points):
        self._equity += pnl_points * self.specs[instrument].point_value
