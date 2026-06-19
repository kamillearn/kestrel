"""IBKR adapter (ib_insync -> TWS/IB Gateway). Primary venue: micro futures.
Places the OCO as two stop-entry parents in one OCA group, each with a child
protective stop (and optional limit target). pip install ib_insync."""
from __future__ import annotations
import os
import pandas as pd
from kestrel.execution.broker import Broker, OcoBracket, Position
from kestrel.instruments import SPECS

class IBKRBroker(Broker):
    def __init__(self, host=None, port=None, client_id=None):
        self.host = host or os.getenv("IB_HOST", "127.0.0.1")
        self.port = int(port or os.getenv("IB_PORT", "4002"))
        self.client_id = int(client_id or os.getenv("IB_CLIENT_ID", "21"))
        self.ib = None; self._c = {}

    def connect(self):
        from ib_insync import IB
        self.ib = IB(); self.ib.connect(self.host, self.port, clientId=self.client_id)
    def disconnect(self):
        if self.ib: self.ib.disconnect()

    def _contract(self, sym):
        from ib_insync import ContFuture, Stock
        if sym not in self._c:
            if sym in ("SPY",):
                c = Stock(sym, "SMART", "USD")
            else:
                # THE FIX: Read the exchange from our SPECS registry!
                exch = SPECS[sym].ibkr_exchange if sym in SPECS else "CME"
                c = ContFuture(sym, exchange=exch)
                
            self.ib.qualifyContracts(c); self._c[sym] = c
        return self._c[sym]

    def equity(self):
        for v in self.ib.accountValues():
            # THE FIX: Allow the engine to detect EUR account balances
            if v.tag == "NetLiquidation" and v.currency in ("USD", "EUR", "BASE"): 
                return float(v.value)
        return 0.0

    def recent_bars(self, instrument, count=800):
        c = self._contract(instrument)
        bars = self.ib.reqHistoricalData(c, "", f"{max(count*60,3600)} S", "1 min",
                                         "TRADES", useRTH=False, formatDate=2)
        df = pd.DataFrame([(b.date,b.open,b.high,b.low,b.close,b.volume) for b in bars],
                          columns=["time","open","high","low","close","volume"])
        df["time"] = pd.to_datetime(df["time"], utc=True); return df.set_index("time")

    def place_oco(self, b: OcoBracket):
        from ib_insync import StopOrder, LimitOrder
        c = self._contract(b.instrument); q = int(round(b.qty))
        if q <= 0: return []
        oca = f"{b.tag}-{b.instrument}"; ids = []
        for side, entry, stop, tgt in [("BUY", b.long_entry, b.long_stop, b.long_target),
                                       ("SELL", b.short_entry, b.short_stop, b.short_target)]:
            parent = StopOrder(side, q, entry)
            parent.ocaGroup = oca; parent.ocaType = 1; parent.transmit = False
            p = self.ib.placeOrder(c, parent); ids.append(str(p.order.orderId))
            opp = "SELL" if side == "BUY" else "BUY"
            sl = StopOrder(opp, q, stop); sl.parentId = parent.orderId
            sl.transmit = tgt is None; self.ib.placeOrder(c, sl)
            if tgt is not None:
                tp = LimitOrder(opp, q, tgt); tp.parentId = parent.orderId
                tp.transmit = True; self.ib.placeOrder(c, tp)
        return ids

    def open_orders(self, instrument):
        return [str(t.order.orderId) for t in self.ib.openTrades()
                if t.contract.symbol == instrument]
    def cancel_all(self, instrument):
        for t in self.ib.openTrades():
            if t.contract.symbol == instrument: self.ib.cancelOrder(t.order)
    def positions(self):
        return [Position(p.contract.symbol, "long" if p.position>0 else "short",
                         abs(p.position), p.avgCost) for p in self.ib.positions() if p.position]
    def flatten(self, instrument):
        from ib_insync import MarketOrder
        for p in self.positions():
            if p.instrument == instrument:
                self.ib.placeOrder(self._contract(instrument),
                    MarketOrder("SELL" if p.side=="long" else "BUY", int(p.qty)))