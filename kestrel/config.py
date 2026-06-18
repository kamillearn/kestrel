"""Config (YAML + env)."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import yaml
from kestrel.risk.manager import RiskConfig

@dataclass
class Config:
    broker: str = "ibkr"            # ibkr | oanda
    instruments: list = field(default_factory=lambda: ["MNQ", "SPY", "MYM"])
    target_R: float | None = None
    poll_seconds: int = 15
    risk: RiskConfig = field(default_factory=RiskConfig)

def load_config(path) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    return Config(broker=raw.get("broker","ibkr"),
                  instruments=raw.get("instruments",["MNQ","SPY","MYM"]),
                  target_R=raw.get("target_R"),
                  poll_seconds=raw.get("poll_seconds",15),
                  risk=RiskConfig(**raw.get("risk",{})))
