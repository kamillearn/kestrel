"""Risk: position sizing + account circuit breakers + file kill-switch.

Sizing is fixed-fractional on the plan's risk (OR width). Breakers halt trading on
a daily loss limit, max trades/day, max concurrent, or a consecutive-loss streak.
A kill-switch file (presence => halt) lets you stop the bot instantly on the VPS.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import date


@dataclass
class RiskConfig:
    risk_per_trade: float = 0.005
    max_daily_loss: float = 0.02
    max_trades_per_day: int = 3
    max_concurrent: int = 2
    max_consecutive_losses: int = 6
    kill_switch_file: str = "KILL"


@dataclass
class RiskState:
    equity: float
    day: date | None = None
    realized_today: float = 0.0
    trades_today: int = 0
    open_positions: int = 0
    consec_losses: int = 0


class RiskManager:
    def __init__(self, cfg: RiskConfig, equity: float):
        self.cfg = cfg
        self.s = RiskState(equity=equity)

    def roll_day(self, d: date):
        if self.s.day != d:
            self.s.day = d
            self.s.realized_today = 0.0
            self.s.trades_today = 0

    def _kill(self) -> bool:
        return os.path.exists(self.cfg.kill_switch_file)

    def can_trade(self) -> tuple[bool, str]:
        c, s = self.cfg, self.s
        if self._kill(): return False, "kill-switch file present"
        if s.realized_today <= -c.max_daily_loss * s.equity: return False, "daily loss limit"
        if s.trades_today >= c.max_trades_per_day: return False, "max trades/day"
        if s.open_positions >= c.max_concurrent: return False, "max concurrent"
        if s.consec_losses >= c.max_consecutive_losses: return False, "loss-streak halt"
        return True, "ok"

    def contracts(self, entry: float, stop: float, point_value: float, step: float) -> float:
        dist = abs(entry - stop)
        if dist <= 0: return 0.0
        raw = (self.s.equity * self.cfg.risk_per_trade) / (dist * point_value)
        n = math.floor(raw / step) * step
        return max(n, 0.0)

    def on_open(self):
        self.s.trades_today += 1
        self.s.open_positions += 1

    def on_close(self, pnl: float):
        self.s.open_positions = max(0, self.s.open_positions - 1)
        self.s.realized_today += pnl
        self.s.equity += pnl
        self.s.consec_losses = self.s.consec_losses + 1 if pnl < 0 else 0
