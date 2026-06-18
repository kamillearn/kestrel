"""OANDA adapter (oandapyV20). Secondary venue / CFDs matching the research data.
Places two STOP entries each with stopLossOnFill; the runner cancels the resting
opposite once one fills (software OCO). pip install oandapyV20."""
from __future__ import annotations
import os
import pandas as pd
from kestrel.execution.broker import Broker, OcoBracket, Position

class OandaBroker(Broker):
    def __init__(self, token=None, account=None, env=None):
        self.token = token or os.getenv("OANDA_TOKEN")
        self.account = account or os.getenv("OANDA_ACCOUNT")
        self.env = (env or os.getenv("OANDA_ENV", "practice")).lower()
        self.api = None

    def connect(self):
        from oandapyV20 import API
        self.api = API(access_token=self.token, environment=self.env)
    def disconnect(self): self.api = None

    def equity(self):
        import oandapyV20.endpoints.accounts as acc
        r = acc.AccountSummary(self.account); self.api.request(r)
        return float(r.response["account"]["NAV"])

    def recent_bars(self, instrument, count=800):
        import oandapyV20.endpoints.instruments as ins
        r = ins.InstrumentsCandles(instrument, params={"granularity":"M1","count":count,"price":"M"})
        self.api.request(r)
        rows = [(c["time"],float(c["mid"]["o"]),float(c["mid"]["h"]),float(c["mid"]["l"]),
                 float(c["mid"]["c"]),int(c["volume"])) for c in r.response["candles"] if c["complete"]]
        df = pd.DataFrame(rows, columns=["time","open","high","low","close","volume"])
        df["time"] = pd.to_datetime(df["time"]); return df.set_index("time")

    def _stop(self, instrument, units, entry, sl, tgt, tag):
        import oandapyV20.endpoints.orders as orders
        o = {"instrument":instrument,"units":str(int(units)),"type":"STOP",
             "price":f"{entry:.5f}","timeInForce":"GTC",
             "stopLossOnFill":{"price":f"{sl:.5f}"},"clientExtensions":{"tag":tag}}
        if tgt is not None: o["takeProfitOnFill"] = {"price":f"{tgt:.5f}"}
        r = orders.OrderCreate(self.account, data={"order":o}); self.api.request(r)
        return r.response.get("orderCreateTransaction",{}).get("id","")

    def place_oco(self, b: OcoBracket):
        q = int(round(b.qty))
        return [self._stop(b.instrument, q, b.long_entry, b.long_stop, b.long_target, b.tag+"L"),
                self._stop(b.instrument, -q, b.short_entry, b.short_stop, b.short_target, b.tag+"S")]

    def open_orders(self, instrument):
        import oandapyV20.endpoints.orders as orders
        r = orders.OrdersPending(self.account); self.api.request(r)
        return [o["id"] for o in r.response.get("orders",[]) if o.get("instrument")==instrument]
    def cancel_all(self, instrument):
        import oandapyV20.endpoints.orders as orders
        for oid in self.open_orders(instrument):
            try: self.api.request(orders.OrderCancel(self.account, oid))
            except Exception: pass
    def positions(self):
        import oandapyV20.endpoints.positions as pos
        r = pos.OpenPositions(self.account); self.api.request(r); out=[]
        for p in r.response.get("positions",[]):
            lu=float(p["long"]["units"]); su=float(p["short"]["units"])
            if lu: out.append(Position(p["instrument"],"long",lu,float(p["long"]["averagePrice"])))
            if su: out.append(Position(p["instrument"],"short",abs(su),float(p["short"]["averagePrice"])))
        return out
    def flatten(self, instrument):
        import oandapyV20.endpoints.positions as pos
        try: self.api.request(pos.PositionClose(self.account, instrument,
            data={"longUnits":"ALL","shortUnits":"ALL"}))
        except Exception: pass
