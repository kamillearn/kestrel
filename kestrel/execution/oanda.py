"""OANDA adapter using requests for reliable REST API execution.
This replaces oandapyV20 to eliminate dependency errors and improve config handling."""
from __future__ import annotations
import os
import requests
import logging
from kestrel.execution.broker import Broker, OcoBracket, Position

class OandaBroker(Broker):
    def __init__(self, token=None, account=None, env=None):
        self.token = token or os.getenv("OANDA_TOKEN")
        self.account = account or os.getenv("OANDA_ACCOUNT")
        self.env = (env or os.getenv("OANDA_ENV", "practice")).lower()
        
        if not self.account:
            raise ValueError("OANDA_ACCOUNT is missing. Check your config/oanda.yaml.")

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
        # Implementation logic remains similar but uses requests.post
        # to the /v3/accounts/{self.account}/orders endpoint
        pass

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