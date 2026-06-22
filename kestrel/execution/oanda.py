"""OANDA adapter using requests for reliable REST API execution.
This replaces oandapyV20 to eliminate dependency errors and improve config handling."""
from __future__ import annotations
import os
import requests
import logging
from kestrel.execution.broker import (Broker, OcoBracket, Position, BracketHandle,
                                      WorkingOrder, OrderKind)

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
        self._prec = {}   # instrument -> displayPrecision (cached)

    def _precision(self, instrument):
        """OANDA rejects prices finer than an instrument's displayPrecision."""
        if instrument not in self._prec:
            try:
                r = requests.get(f"{self.host}/v3/accounts/{self.account}/instruments",
                                 headers=self.headers, params={"instruments": instrument})
                self._prec[instrument] = int(r.json()["instruments"][0]["displayPrecision"])
            except Exception:
                self._prec[instrument] = 5
        return self._prec[instrument]

    def _px(self, instrument, price):
        return f"{float(price):.{self._precision(instrument)}f}"

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
        """Two STOP entry orders (v20 has no native OCO). Each carries a
        ``stopLossOnFill`` so the protective stop lives server-side the instant
        the entry fills. Software-OCO (cancel the resting opposite) is done by
        the runner's manage loop. Returns the entry order ids; the child stop ids
        are created on fill and discovered later via ``working_orders``."""
        handle = BracketHandle(instrument=b.instrument)
        units = int(round(b.qty))
        if units <= 0:
            return handle

        # 1. Define our symbol fallbacks (v20 standard, Base only, UI standard)
        base_asset = b.instrument.split('_')[0] if '_' in b.instrument else b.instrument.split('/')[0]
        quote_asset = b.instrument.split('_')[1] if '_' in b.instrument else 'USD'
        
        symbols_to_try = [
            b.instrument,
            f"{base_asset}_{quote_asset}",  # e.g., SPX500_USD (Standard v20)
            base_asset,                     # e.g., SPX500
            f"{base_asset}/{quote_asset}"   # e.g., SPX500/USD
        ]
        # Remove duplicates while preserving order
        symbols_to_try = list(dict.fromkeys(symbols_to_try))

        legs = [("long", b.long_entry, b.long_stop, units),
                ("short", b.short_entry, b.short_stop, -units)]

        for side, entry, stop, signed in legs:
            if side not in b.allowed_sides:
                continue   # trend filter narrowed this bracket to one side
            
            order_success = False
            last_error = ""

            for sym in symbols_to_try:
                order = {"order": {
                    "type": "STOP",
                    "instrument": sym,
                    "units": str(signed),
                    "price": self._px(sym, entry),
                    "timeInForce": "GTC",
                    "stopLossOnFill": {"price": self._px(sym, stop), "timeInForce": "GTC"},
                    "clientExtensions": {"tag": b.tag, "comment": f"kestrel-{side}"},
                }}
                
                r = requests.post(f"{self.host}/v3/accounts/{self.account}/orders",
                                  headers=self.headers, json=order)
                response = r.json()

                # 2. STRICT REJECTION CHECKING
                if "orderRejectTransaction" in response:
                    reject_reason = response["orderRejectTransaction"].get("rejectReason", "UNKNOWN_REASON")
                    logging.error(f"❌ OANDA REJECTED {side.upper()} ORDER for {sym} | Reason: {reject_reason}")
                    
                    # If it's rejected for price/distance, the symbol is correct, the math is wrong. Break.
                    raise ValueError(f"Broker rejected {side} terms: {reject_reason}")

                # 3. Check for invalid symbol or other API errors
                if "errorMessage" in response:
                    error_msg = response.get("errorMessage", "")
                    if "Invalid value specified for 'instrument'" in error_msg or "Invalid instrument" in error_msg:
                        logging.warning(f"⚠️ Symbol {sym} unrecognized by OANDA. Trying fallback...")
                        last_error = error_msg
                        continue
                    else:
                        # Other errors like insufficient margin
                        logging.error(f"❌ API Execution Error for {sym}: {error_msg}")
                        raise ValueError(f"OANDA API Error: {error_msg}")

                # If we get here, it succeeded!
                oid = response.get("orderCreateTransaction", {}).get("id")
                if oid:
                    logging.info(f"✅ OCO {side.upper()} Leg Deployed Successfully! (ID: {oid})")
                    if side == "long":
                        handle.long_entry_id = str(oid)
                    else:
                        handle.short_entry_id = str(oid)
                    handle.instrument = sym  # Save the working symbol
                    order_success = True
                    break
            
            # If the loop finishes and all symbols failed
            if not order_success:
                logging.error(f"❌ Order completely failed for {side}. Last error: {last_error}")
                raise ValueError(f"All symbol formats invalid for {b.instrument}.")

        return handle

    # ---- adaptive-management (OANDA v20 REST) ----

    def cancel_order(self, instrument, order_id):
        """Cancel a single resting order (software-OCO / time-decay)."""
        requests.put(f"{self.host}/v3/accounts/{self.account}/orders/{order_id}/cancel",
                     headers=self.headers)

    def modify_stop(self, instrument, stop_order_id, new_stop_price):
        """Replace a STOP_LOSS order's price. OANDA's PUT /orders/{id} cancels the
        old order and creates a new one (NEW id) bound to the same trade — return
        that new id so the manager keeps tracking the live stop."""
        existing = requests.get(f"{self.host}/v3/accounts/{self.account}/orders/{stop_order_id}",
                                headers=self.headers).json().get("order", {})
        body = {"order": {"type": "STOP_LOSS",
                          "price": self._px(instrument, new_stop_price),
                          "timeInForce": "GTC"}}
        trade_id = existing.get("tradeID")
        if trade_id:
            body["order"]["tradeID"] = trade_id
        r = requests.put(f"{self.host}/v3/accounts/{self.account}/orders/{stop_order_id}",
                         headers=self.headers, json=body)
        new_id = r.json().get("orderCreateTransaction", {}).get("id")
        return str(new_id) if new_id else str(stop_order_id)

    def working_orders(self, instrument):
        """Resting entry STOP orders (pendingOrders) plus the protective STOP_LOSS
        attached to any open trade for this instrument."""
        out = []
        po = requests.get(f"{self.host}/v3/accounts/{self.account}/pendingOrders",
                          headers=self.headers).json()
        for o in po.get("orders", []):
            if o.get("instrument") != instrument:
                continue
            if o.get("type") in ("STOP", "MARKET_IF_TOUCHED", "LIMIT"):
                units = float(o.get("units", 0))
                out.append(WorkingOrder(str(o["id"]), instrument, OrderKind.ENTRY,
                                        "long" if units > 0 else "short",
                                        float(o.get("price", 0) or 0), abs(units)))
        ot = requests.get(f"{self.host}/v3/accounts/{self.account}/openTrades",
                          headers=self.headers).json()
        for t in ot.get("trades", []):
            if t.get("instrument") != instrument:
                continue
            sl = t.get("stopLossOrder")
            if sl:
                cu = float(t.get("currentUnits", 0))
                out.append(WorkingOrder(str(sl["id"]), instrument, OrderKind.STOP,
                                        "long" if cu > 0 else "short",
                                        float(sl.get("price", 0) or 0), abs(cu)))
        return out

    def last_price(self, instrument):
        """Mid price snapshot for the +1R break-even check."""
        try:
            r = requests.get(f"{self.host}/v3/accounts/{self.account}/pricing",
                             headers=self.headers, params={"instruments": instrument})
            p = r.json()["prices"][0]
            return (float(p["bids"][0]["price"]) + float(p["asks"][0]["price"])) / 2.0
        except Exception:
            bars = self.recent_bars(instrument, count=2)
            return float(bars["close"].iloc[-1]) if len(bars) else float("nan")

    def recent_daily_closes(self, instrument, n):
        """Completed daily mid closes (oldest first) for the trend filter."""
        import pandas as pd
        r = requests.get(f"{self.host}/v3/instruments/{instrument}/candles",
                         headers=self.headers,
                         params={"granularity": "D", "count": n + 5, "price": "M"})
        closes = [float(c["mid"]["c"]) for c in r.json().get("candles", []) if c["complete"]]
        return pd.Series(closes)

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