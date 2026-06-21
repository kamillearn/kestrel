"""Test suite. Run: PYTHONPATH=. pytest -q"""
import datetime
import numpy as np
import pandas as pd

from kestrel.utils.sessions import to_eastern
from kestrel.instruments import get_spec
from kestrel.strategy.orb import ORBStrategy
from kestrel.engine.backtester import backtest
from kestrel.risk.manager import RiskManager, RiskConfig
from kestrel.execution.oanda import OandaBroker
from kestrel.execution.ibkr import IBKRBroker
from kestrel.execution.broker import BracketHandle

from scripts.run import InstrRuntime, Status, save_state, load_state, broker_symbol


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


def test_trend_filter_is_subset():
    # the trend filter can only ever REMOVE trades, never add or flip them
    df = to_eastern(_rising_day(), "time")
    spec = get_spec("MNQ")
    assert len(backtest(df, spec, trend_sma=20)) <= len(backtest(df, spec))


def test_risk_sizing_and_kill_switch(tmp_path):
    ks = tmp_path / "KILL"
    rm = RiskManager(RiskConfig(risk_per_trade=0.01, kill_switch_file=str(ks)), equity=10000)
    qty = rm.contracts(entry=18050, stop=18000, point_value=2.0, step=1.0)  # 50pt risk, $2/pt
    assert qty == 1.0                                       # 100$ risk / (50*2) = 1.0
    ok, _ = rm.can_trade(); assert ok
    ks.write_text("stop")
    ok, why = rm.can_trade(); assert not ok and "kill" in why


def test_risk_circuit_breakers():
    rm = RiskManager(RiskConfig(max_trades_per_day=2, max_concurrent=5), equity=10000)
    rm.roll_day(datetime.date(2024, 6, 3))
    assert rm.can_trade()[0]
    rm.on_open(); rm.on_close(-50.0)        # trade 1 (a loss)
    assert rm.can_trade()[0]
    rm.on_open(); rm.on_close(10.0)         # trade 2
    assert not rm.can_trade()[0]            # max_trades_per_day=2 reached


def test_broker_symbol_mapping():
    oanda = OandaBroker(account_id="acc", token="tok", env="practice")
    ibkr = IBKRBroker()
    assert broker_symbol(oanda, "MNQ") == "NAS100_USD"   # OANDA CFD symbol
    assert broker_symbol(ibkr, "MNQ") == "MNQ"           # IBKR futures symbol


def test_durable_state_roundtrip(tmp_path):
    p = str(tmp_path / "logs" / "state.json")
    ds = {"MNQ": InstrRuntime(status=Status.IN_TRADE,
                              handle=BracketHandle("MNQ", long_stop_id="SL1"),
                              side="long", be_moved=True, qty=2.0)}
    save_state(ds, "2026-06-21", path=p)
    back = load_state("2026-06-21", ["MNQ"], path=p)
    assert back is not None
    assert back["MNQ"].status == Status.IN_TRADE and back["MNQ"].be_moved
    assert back["MNQ"].handle.stop_id_for("long") == "SL1"   # nested dataclass survived
    assert load_state("2026-06-22", ["MNQ"], path=p) is None  # date mismatch -> fresh
