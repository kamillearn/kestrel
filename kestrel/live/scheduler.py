"""Session phase clock (ET). Tells the runner what to do without per-tick guessing."""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from kestrel.utils.sessions import Session, ET

class Phase(str, Enum):
    PRE_OPEN = "pre_open"
    OPENING_RANGE = "opening_range"
    ACTIVE = "active"          # OR complete -> place/monitor orders
    FLATTEN = "flatten"        # square off
    CLOSED = "closed"

def phase(session: Session, now: datetime | None = None) -> Phase:
    now = now or datetime.now(ET)
    if now.weekday() >= 5:
        return Phase.CLOSED
    m = now.hour * 60 + now.minute
    if m < session.open_m: return Phase.PRE_OPEN
    if m < session.or_end_m: return Phase.OPENING_RANGE
    if m < session.flatten_m: return Phase.ACTIVE
    if m < session.close_m: return Phase.FLATTEN
    return Phase.CLOSED
