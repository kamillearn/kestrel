"""Test suite. Run: PYTHONPATH=. pytest -q"""
import numpy as np
import pandas as pd
from datetime import datetime

from kestrel.utils.sessions import to_eastern, US_EQUITY, ET
from kestrel.instruments import get_spec
from kestrel.strategy.orb import ORBStrategy
from kestrel.engine.backtester import backtest
from kestrel.risk.manager import RiskManager, RiskConfig
from kestrel.live.scheduler import phase, Phase


def _rising_day(date="2024-06-03"):
    idx = pd.date_range(f"{date} 13:30", f"{date} 20:00", freq="1min", tz="UTC")
    base = 18000 + np.linspace(0, 80, len(idx))
    return pd.DataFrame({"time": idx, "open": base, "high": base + 3,
                         "low": base - 3, "close": base + 1,
                         "volume": 100, "spread": 0.25})


def test_dst_open_is_0930():
    df = to_eastern(_rising_day(), "time")
    assert (df.index[0].hour, df.index[0].minute) == (9, 30)


def test_orb_plan_and_long_breakout():
    df = to_eastern(_rising_day(), "time")
    spec = get_spec("MNQ")
    plan = ORBStrategy(spec).build_plan(df[df.etdate == df.index[0].date()], df.index[0].date())
    assert plan is not None and plan.valid
    tr = backtest(df, spec)
    assert len(tr) == 1 and tr.iloc[0]["side"] == "long"     # rising day -> long
    assert tr.iloc[0]["risk"] > 0


def test_risk_sizing_and_kill_switch(tmp_path):
    ks = tmp_path / "KILL"
    rm = RiskManager(RiskConfig(risk_per_trade=0.01, kill_switch_file=str(ks)), equity=10000)
    qty = rm.contracts(entry=18050, stop=18000, point_value=2.0, step=1.0)  # 50pt risk, $2/pt
    assert qty == 1.0                                       # 100$ risk / (50*2) = 1.0
    ok, _ = rm.can_trade(); assert ok
    ks.write_text("stop")
    ok, why = rm.can_trade(); assert not ok and "kill" in why


def test_phases():
    s = US_EQUITY
    assert phase(s, datetime(2024, 6, 3, 9, 0, tzinfo=ET)) == Phase.PRE_OPEN
    assert phase(s, datetime(2024, 6, 3, 9, 45, tzinfo=ET)) == Phase.OPENING_RANGE
    assert phase(s, datetime(2024, 6, 3, 12, 0, tzinfo=ET)) == Phase.ACTIVE
    assert phase(s, datetime(2024, 6, 3, 15, 58, tzinfo=ET)) == Phase.FLATTEN
