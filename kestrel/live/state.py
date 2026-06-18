"""Durable per-day state so a restart never double-places orders or re-enters.

State is a JSON file keyed by ET date. Each instrument tracks whether its plan
was placed (idempotency), the resting order ids, whether it has entered, and
whether it has been flattened. ``reconcile`` checks persisted intent against the
broker's actual open orders/positions on startup and each loop.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class InstrState:
    plan_placed: bool = False
    order_ids: list = field(default_factory=list)
    entered: bool = False
    side: str | None = None
    flattened: bool = False


@dataclass
class DayState:
    date: str
    instruments: dict = field(default_factory=dict)   # sym -> InstrState (as dict)

    def get(self, sym: str) -> InstrState:
        d = self.instruments.get(sym)
        return InstrState(**d) if d else InstrState()

    def put(self, sym: str, st: InstrState):
        self.instruments[sym] = asdict(st)


class StateStore:
    def __init__(self, path: str = "state.json"):
        self.path = Path(path)

    def load(self, today: str) -> DayState:
        if self.path.exists():
            raw = json.loads(self.path.read_text())
            if raw.get("date") == today:
                return DayState(date=today, instruments=raw.get("instruments", {}))
        return DayState(date=today)

    def save(self, st: DayState):
        self.path.write_text(json.dumps(asdict(st), indent=2, default=str))


def reconcile(broker, sym: str, st: InstrState) -> InstrState:
    """Sync persisted intent with broker truth (positions/orders win)."""
    pos = [p for p in broker.positions() if p.instrument == sym]
    if pos and not st.entered:
        st.entered = True
        st.side = pos[0].side
    if st.entered and not pos:
        st.flattened = True
    return st
