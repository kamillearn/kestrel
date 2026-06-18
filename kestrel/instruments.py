"""Instrument specs: how to size, what it costs, and what each broker calls it.

``point_value`` = account-currency P&L per 1.0 price-point per 1 unit/contract.
``slippage`` = assumed adverse fill per side in *points* (used in backtests and
the paper broker). These are deliberately conservative for breakout (stop) fills.
"""
from __future__ import annotations

from dataclasses import dataclass

from kestrel.utils.sessions import Session, US_EQUITY


@dataclass(frozen=True)
class InstrumentSpec:
    key: str                     # internal name, e.g. 'MNQ'
    point_value: float           # $ per point per contract/unit
    tick: float                  # min price increment
    slippage: float              # assumed adverse fill per side, in points
    contract_step: float = 1.0   # min order size (1 contract / fractional units)
    session: Session = US_EQUITY
    ibkr_symbol: str | None = None
    oanda_symbol: str | None = None


# Validated/recommended set. Single stocks intentionally excluded (ORB fails on them).
SPECS: dict[str, InstrumentSpec] = {
    "MNQ":  InstrumentSpec("MNQ", point_value=2.0,  tick=0.25, slippage=0.25,
                           contract_step=1.0, ibkr_symbol="MNQ", oanda_symbol="NAS100_USD"),
    "MYM":  InstrumentSpec("MYM", point_value=0.5,  tick=1.0,  slippage=1.0,
                           contract_step=1.0, ibkr_symbol="MYM", oanda_symbol="US30_USD"),
    "SPY":  InstrumentSpec("SPY", point_value=1.0,  tick=0.01, slippage=0.02,
                           contract_step=1.0, ibkr_symbol="SPY", oanda_symbol="SPX500_USD"),
}


def get_spec(key: str) -> InstrumentSpec:
    if key not in SPECS:
        raise KeyError(f"unknown instrument '{key}'. known: {list(SPECS)}")
    return SPECS[key]
