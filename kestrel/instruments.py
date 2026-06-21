"""Instrument specs: how to size, what it costs, and what each broker calls it."""
from __future__ import annotations
from dataclasses import dataclass
from kestrel.utils.sessions import Session, US_EQUITY, EU_EQUITY, UK_EQUITY, HK_EQUITY, AU_EQUITY, ASIA_EQUITY

@dataclass(frozen=True)
class InstrumentSpec:
    key: str
    point_value: float
    tick: float
    slippage: float
    contract_step: float = 1.0
    session: Session = US_EQUITY
    ibkr_symbol: str | None = None
    oanda_symbol: str | None = None
    ibkr_exchange: str = "CME" # THE FIX: Default to CME, allow overrides

SPECS: dict[str, InstrumentSpec] = {
    # --- US MARKETS (Tier 1, Tier 2, & Backups) ---
    
    # MNQ (Micro E-Mini Nasdaq-100): $2 per point, 0.25 tick size
    "MNQ":  InstrumentSpec("MNQ", point_value=2.0,  tick=0.25, slippage=0.25,
                           contract_step=1.0, ibkr_symbol="MNQ", oanda_symbol="NAS100_USD"),
                           
    # NQ (E-Mini Nasdaq-100): $20 per point, 0.25 tick size
    "NQ":   InstrumentSpec("NQ", point_value=20.0, tick=0.25, slippage=0.25,
                           contract_step=1.0, ibkr_symbol="NQ", oanda_symbol="NAS100_USD"),

    # MES (Micro E-Mini S&P 500): $5 per point, 0.25 tick size
    "MES":  InstrumentSpec("MES", point_value=5.0,  tick=0.25, slippage=0.25,
                           contract_step=1.0, ibkr_symbol="MES", oanda_symbol="SPX500_USD"),

    # ES (E-Mini S&P 500): $50 per point, 0.25 tick size
    "ES":   InstrumentSpec("ES", point_value=50.0, tick=0.25, slippage=0.25,
                           contract_step=1.0, ibkr_symbol="ES", oanda_symbol="SPX500_USD"),

    # M2K (Micro Russell 2000): $5 per point, 0.10 tick size
    "M2K":  InstrumentSpec("M2K", point_value=5.0, tick=0.10, slippage=0.20,
                           contract_step=1.0, ibkr_symbol="M2K", oanda_symbol="US2000_USD"),

    # RTY (Russell 2000 E-Mini): $50 per point, 0.10 tick size
    "RTY":  InstrumentSpec("RTY", point_value=50.0, tick=0.10, slippage=0.20,
                           contract_step=1.0, ibkr_symbol="RTY", oanda_symbol="US2000_USD"),

    # SPY (S&P 500 ETF Proxy)
    "SPY":  InstrumentSpec("SPY", point_value=1.0,  tick=0.01, slippage=0.02,
                           contract_step=1.0, ibkr_symbol="SPY", oanda_symbol="SPX500_USD"),

    # --- EUROPEAN MARKETS (Tier 1 Diversifier) ---
    
    # FDXS (Micro DAX Index): €1 per point, 1.0 tick size
    "FDXS": InstrumentSpec("FDXS", point_value=1.0, tick=1.0, slippage=1.0,
                           session=EU_EQUITY, ibkr_symbol="FDXS", oanda_symbol="DE30_EUR", ibkr_exchange="EUREX"),

    # DAX (Full DAX Index): €25 per point, 1.0 tick size
    "DAX":  InstrumentSpec("DAX", point_value=25.0, tick=1.0, slippage=1.0,
                           session=EU_EQUITY, ibkr_symbol="DAX", oanda_symbol="DE30_EUR", ibkr_exchange="EUREX"),

    # --- GLOBAL MARKET ALTERNATIVES ---

    # ESTX50 (Euro Stoxx 50 Index): €10 per point, 1.0 tick size
    "ESTX50": InstrumentSpec("ESTX50", point_value=10.0, tick=1.0, slippage=1.0,
                             session=EU_EQUITY, ibkr_symbol="ESTX50", ibkr_exchange="EUREX"),

    # Z (UK FTSE 100 Index): £10 per point, 0.5 tick size — London session
    "Z":      InstrumentSpec("Z", point_value=10.0, tick=0.5, slippage=0.5,
                             session=UK_EQUITY, ibkr_symbol="Z"),

    # HSI (Hang Seng Index): HKD 50 per point, 1.0 tick size — Hong Kong session
    "HSI":    InstrumentSpec("HSI", point_value=50.0, tick=1.0, slippage=2.0,
                             session=HK_EQUITY, ibkr_symbol="HSI"),

    # AP (ASX SPI 200): AUD 25 per point, 1.0 tick size — Sydney session
    "AP":     InstrumentSpec("AP", point_value=25.0, tick=1.0, slippage=1.0,
                             session=AU_EQUITY, ibkr_symbol="AP"),
}

def get_spec(key: str) -> InstrumentSpec:
    if key not in SPECS:
        raise KeyError(f"unknown instrument '{key}'. known: {list(SPECS)}")
    return SPECS[key]