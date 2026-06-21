"""OANDA adapter using requests for reliable REST API execution.
This replaces oandapyV20 to eliminate dependency errors and improve config handling."""
from __future__ import annotations
import os
import requests
import logging
from kestrel.execution.broker import Broker, OcoBracket, Position, BracketHandle

class OandaBroker(Broker):
    def __init__(self, token=None, account_id=None, env=None):
        self.token = token or os.getenv("OANDA_TOKEN")
        # Ensure we check the passed account_id argument first
        self.account = account_id or os.getenv("OANDA_ACCOUNT")
        self.env = (env or os.getenv("OANDA_ENV", "practice")).lower()
        
        if not self.account:
            raise ValueError("OANDA_ACCOUNT is missing. Check your config/oanda.yaml or environment variables.")

        self.host = "https://api-fxpractice.oanda.com" if self.env == "practice" else "https://api-fxtrade.oanda.com"
        self.headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def connect(self):
        """Verify connection to OANDA by fetching account summary."""
        url = f"{self.host}/v3/accounts/{self.account}/summary"
        r = requests.get(url, headers=self.headers)
        if r.status_code != 200:
            raise ConnectionError(f"OANDA Connection failed: {r.text}")
        logging.info(f"Connected to OANDA Account {self.account}")

    def disconnect(self):
        pass

    def equity(self):
        url = f"{self.host}/v3/accounts/{self.account}/summary"
        r = requests.get(url, headers=self.headers)
        if r.status_code == 200:
            return float(r.json()["account"]["NAV"])
        return 0.0

    def recent_bars(self, instrument, count=800):
        url = f"{self.host}/v3/instruments/{instrument}/candles"
        params = {"granularity": "M1", "count": count, "price": "M"}
        r = requests.get(url, headers=self.headers, params=params)
        data = r.json().get("candles", [])
        
        rows = [(c["time"], float(c["mid"]["o"]), float(c["mid"]["h"]), 
                 float(c["mid"]["l"]), float(c["mid"]["c"]), int(c["volume"])) 
                for c in data if c["complete"]]
        
        import pandas as pd
        df = pd.DataFrame(rows, columns=["time","open","high","low","close","volume"])
        df["time"] = pd.to_datetime(df["time"])
        return df.set_index("time")

    def place_oco(self, b: OcoBracket):
        # NOT IMPLEMENTED: live OANDA execution is still a stub. Returns an empty
        # handle (not None) so the new BracketHandle contract holds and dry-runs
        # don't crash. v20 has no native OCA — a real impl needs software-OCO.
        return BracketHandle(instrument=b.instrument)

    # ---- adaptive-management stubs (see IBKR for the real implementations) ----

    def cancel_order(self, instrument, order_id):
        raise NotImplementedError("OANDA cancel_order not implemented yet")

    def modify_stop(self, instrument, stop_order_id, new_stop_price):
        raise NotImplementedError("OANDA modify_stop not implemented yet")

    def working_orders(self, instrument):
        raise NotImplementedError("OANDA working_orders not implemented yet")

    def recent_daily_closes(self, instrument, n):
        raise NotImplementedError("OANDA recent_daily_closes not implemented yet")

    def open_orders(self, instrument):
        url = f"{self.host}/v3/accounts/{self.account}/pendingOrders"
        r = requests.get(url, headers=self.headers)
        return [o["id"] for o in r.json().get("orders", []) if o.get("instrument") == instrument]

    def cancel_all(self, instrument):
        for oid in self.open_orders(instrument):
            url = f"{self.host}/v3/accounts/{self.account}/orders/{oid}/cancel"
            requests.put(url, headers=self.headers)

    def positions(self):
        url = f"{self.host}/v3/accounts/{self.account}/openPositions"
        r = requests.get(url, headers=self.headers)
        out = []
        for p in r.json().get("positions", []):
            lu = float(p.get("long", {}).get("units", 0))
            su = float(p.get("short", {}).get("units", 0))
            if lu: out.append(Position(p["instrument"], "long", lu, float(p["long"]["averagePrice"])))
            if su: out.append(Position(p["instrument"], "short", abs(su), float(p["short"]["averagePrice"])))
        return out

    def flatten(self, instrument):
        url = f"{self.host}/v3/accounts/{self.account}/positions/{instrument}/close"
        requests.put(url, headers=self.headers, json={"longUnits": "ALL", "shortUnits": "ALL"})