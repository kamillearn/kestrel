"""IBKR adapter (ib_insync -> TWS/IB Gateway). Primary venue: micro futures.
Places the OCO as two stop-entry parents in one OCA group, each with a child
protective stop (and optional limit target). pip install ib_insync."""
from __future__ import annotations
import os
import pandas as pd
from kestrel.execution.broker import (Broker, OcoBracket, Position,
                                      BracketHandle, WorkingOrder, OrderKind)
from kestrel.instruments import SPECS

class IBKRBroker(Broker):
    def __init__(self, host=None, port=None, client_id=None):
        self.host = host or os.getenv("IB_HOST", "127.0.0.1")
        self.port = int(port or os.getenv("IB_PORT", "4002"))
        self.client_id = int(client_id or os.getenv("IB_CLIENT_ID", "21"))
        self.ib = None; self._c = {}
        # orderId(str) -> {"order": Order, "contract": Contract, "kind", "side"}
        # so modify_stop / cancel_order can act on the exact ib_insync order object.
        self._orders = {}

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

    def recent_daily_closes(self, instrument, n):
        """Completed daily RTH closes (oldest first) for the trend filter."""
        c = self._contract(instrument)
        bars = self.ib.reqHistoricalData(c, "", f"{n + 5} D", "1 day",
                                         "TRADES", useRTH=True, formatDate=2)
        closes = [float(bar.close) for bar in bars]
        return pd.Series(closes)

    def place_oco(self, b: OcoBracket):
        from ib_insync import StopOrder, LimitOrder
        c = self._contract(b.instrument); q = int(round(b.qty))
        handle = BracketHandle(instrument=b.instrument)
        if q <= 0: return handle
        oca = f"{b.tag}-{b.instrument}"
        for side, action, entry, stop, tgt in [
                ("long", "BUY", b.long_entry, b.long_stop, b.long_target),
                ("short", "SELL", b.short_entry, b.short_stop, b.short_target)]:
            if side not in b.allowed_sides:
                continue   # trend filter narrowed this bracket to one side
            # parent stop-entry, joined to the OCA group (first to fill cancels the other)
            parent = StopOrder(action, q, entry)
            parent.ocaGroup = oca; parent.ocaType = 1; parent.transmit = False
            p = self.ib.placeOrder(c, parent); eid = str(p.order.orderId)
            self._orders[eid] = {"order": parent, "contract": c,
                                 "kind": OrderKind.ENTRY, "side": side}
            opp = "SELL" if action == "BUY" else "BUY"
            # protective stop (child of the entry) — capture its id this time
            sl = StopOrder(opp, q, stop); sl.parentId = parent.orderId
            sl.transmit = tgt is None
            s = self.ib.placeOrder(c, sl); sid = str(s.order.orderId)
            self._orders[sid] = {"order": sl, "contract": c,
                                 "kind": OrderKind.STOP, "side": side}
            tid = None
            if tgt is not None:
                tp = LimitOrder(opp, q, tgt); tp.parentId = parent.orderId
                tp.transmit = True
                t = self.ib.placeOrder(c, tp); tid = str(t.order.orderId)
                self._orders[tid] = {"order": tp, "contract": c,
                                     "kind": OrderKind.TARGET, "side": side}
            if side == "long":
                handle.long_entry_id, handle.long_stop_id, handle.long_target_id = eid, sid, tid
            else:
                handle.short_entry_id, handle.short_stop_id, handle.short_target_id = eid, sid, tid
        return handle

    def open_orders(self, instrument):
        return [str(t.order.orderId) for t in self.ib.openTrades()
                if t.contract.symbol == instrument]

    def cancel_order(self, instrument, order_id):
        """Cancel a single resting order, leaving its siblings (e.g. a live stop)
        in place. Used by the time-decay cancel and software-OCO."""
        rec = self._orders.get(str(order_id))
        if rec is not None:
            self.ib.cancelOrder(rec["order"]); return
        for t in self.ib.openTrades():        # fallback: locate by orderId
            if str(t.order.orderId) == str(order_id):
                self.ib.cancelOrder(t.order); return

    def modify_stop(self, instrument, stop_order_id, new_stop_price):
        """Move a protective stop in place: re-transmit the SAME order object with
        a new auxPrice. TWS treats a re-placed orderId as an amend, so there is no
        cancel/replace gap where the position would sit unprotected."""
        rec = self._orders.get(str(stop_order_id))
        order = contract = None
        if rec is not None:
            order, contract = rec["order"], rec["contract"]
        else:
            for t in self.ib.openTrades():    # fallback: adopt the working order
                if str(t.order.orderId) == str(stop_order_id):
                    order, contract = t.order, t.contract; break
        if order is None:
            raise ValueError(f"unknown stop order {stop_order_id} for {instrument}")
        order.auxPrice = float(new_stop_price)   # StopOrder trigger lives in auxPrice
        order.transmit = True                     # modify-in-place, same orderId
        self.ib.placeOrder(contract, order)
        return str(order.orderId)

    def working_orders(self, instrument):
        out = []
        for t in self.ib.openTrades():
            if t.contract.symbol != instrument:
                continue
            o = t.order; rec = self._orders.get(str(o.orderId), {})
            price = getattr(o, "auxPrice", 0.0) or getattr(o, "lmtPrice", 0.0)
            out.append(WorkingOrder(id=str(o.orderId), instrument=instrument,
                                    kind=rec.get("kind", OrderKind.ENTRY),
                                    side=rec.get("side", ""),
                                    price=float(price or 0.0),
                                    qty=float(o.totalQuantity)))
        return out

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