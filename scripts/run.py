"""
Kestrel Engine - Live Production Entry Point.
This script loads the configuration and initializes the broker/strategy.
"""
import logging
import argparse
import yaml
import time
from datetime import datetime
import math

from kestrel.execution.ibkr import IBKRBroker
from kestrel.execution.oanda import OandaBroker
from kestrel.execution.broker import OcoBracket
from kestrel.instruments import SPECS
from kestrel.utils.sessions import ET

def make_broker(cfg):
    """Instantiates the correct broker based on the nested YAML config."""
    broker_name = cfg.get("broker", {}).get("name", "").lower()
    
    if broker_name == "ibkr":
        host = cfg["broker"].get("host", "127.0.0.1")
        port = cfg["broker"].get("port", 7497)
        client_id = cfg["broker"].get("client_id", 1)
        return IBKRBroker(host=host, port=port, client_id=client_id)
        
    elif broker_name == "oanda":
        broker_cfg = cfg.get("broker", {})
        env = broker_cfg.get("env", "practice")
        token = broker_cfg.get("token")
        account_id = broker_cfg.get("account_id")
        return OandaBroker(env=env, account_id=account_id, token=token)
    else:
        raise ValueError(f"Unknown broker requested in config: '{broker_name}'")

def get_strategy_config(cfg, instrument_key):
    """Extracts specific strategy overrides for an instrument from the config."""
    assets = cfg.get("strategy", {}).get("assets", [])
    for asset in assets:
        if asset.get("key") == instrument_key:
            return asset
    return {}

# --- MAIN EXECUTION LOGIC ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config_path", help="Path to the YAML config file")
    parser.add_argument("--live", action="store_true", help="Run in live execution mode")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    logging.info(f"Loading config from: {args.config_path}")
    with open(args.config_path, 'r') as f:
        cfg = yaml.safe_load(f)

    broker = make_broker(cfg)
    broker.connect()
    
    starting_equity = broker.equity()
    logging.info(f"Pre-flight successful. Live Equity: ${starting_equity:,.2f}")
    
    instruments_to_watch = cfg.get("instruments", [])
    logging.info(f"Loaded Portfolio: {instruments_to_watch}")
    
    # Internal Engine State tracker
    current_date = None
    daily_state = {}

    logging.info("🧠 Kestrel Engine Brain Wired. Entering continuous execution loop.")
    loop_count = 0
    
    try:
        while True:
            # 1. TIME ALIGNMENT (Always use Eastern Time for the Wall Street clock)
            now_et = datetime.now(ET)
            today = now_et.date()
            now_m = now_et.hour * 60 + now_et.minute
            
            # 2. DAILY STATE RESET (If a new day has started)
            if current_date != today:
                current_date = today
                daily_state = {inst: {"status": "WAITING_FOR_OPEN", "oco_ids": []} for inst in instruments_to_watch}
                logging.info(f"🌅 New Trading Session Started: {current_date}")

            # 3. EMERGENCY KILLSWITCH CHECK
            if read_killswitch():
                if loop_count % 60 == 0:
                    logging.warning("🛑 KILLSWITCH ACTIVE. Engine is halted and bypassing all logic.")
                loop_count += 1
                time.sleep(1)
                continue

            # 4. ITERATE THROUGH PORTFOLIO
            for inst in instruments_to_watch:
                state = daily_state[inst]
                if state["status"] == "FLATTENED":
                    continue # Done for the day
                    
                spec = SPECS[inst]
                strat_cfg = get_strategy_config(cfg, inst)
                
                # Use custom OR minutes from config if provided, else use registry default
                or_mins = strat_cfg.get("or_minutes", spec.session.or_minutes)
                
                # Use session times
                open_m = spec.session.open_m
                or_end_m = open_m + or_mins
                flatten_m = spec.session.flatten_m

                # PHASE A: FLATTEN (End of Day)
                if now_m >= flatten_m and state["status"] != "FLATTENED":
                    logging.info(f"[{inst}] 🔔 End of Session reached. Flattening all positions and orders.")
                    broker.cancel_all(inst)
                    broker.flatten(inst)
                    state["status"] = "FLATTENED"
                    continue

                # PHASE B: OPENING RANGE BREAKOUT (ORB) CALCULATION & EXECUTION
                if now_m >= or_end_m and state["status"] == "WAITING_FOR_OPEN":
                    if not args.live:
                        logging.info(f"[{inst}] DRY RUN: Would execute {or_mins}-min Opening Range breakout here.")
                        state["status"] = "OCO_PLACED"
                        continue

                    logging.info(f"[{inst}] ⏳ Opening Range ({or_mins}m) completed. Calculating edge...")
                    
                    # Fetch recent bars to find the High/Low of the opening range
                    # We fetch 60 minutes just to be safe, then slice the exact OR window
                    bars = broker.recent_bars(inst, count=60)
                    
                    if bars.empty:
                        logging.error(f"[{inst}] Failed to fetch recent bars. Retrying next loop.")
                        continue
                        
                    # Filter bars strictly to the Opening Range period for today
                    bars["et_time"] = bars.index.tz_convert(ET)
                    or_bars = bars[
                        (bars["et_time"].dt.date == today) & 
                        (bars["et_time"].dt.hour * 60 + bars["et_time"].dt.minute >= open_m) &
                        (bars["et_time"].dt.hour * 60 + bars["et_time"].dt.minute < or_end_m)
                    ]
                    
                    if or_bars.empty:
                        logging.warning(f"[{inst}] No data found for the Opening Range period today.")
                        continue
                        
                    or_high = float(or_bars["high"].max())
                    or_low = float(or_bars["low"].min())
                    or_width = or_high - or_low
                    
                    if or_width <= 0:
                        continue # Bad data protection
                    
                    # Sizing Math: Risk Amount / (OR Width * Point Value)
                    risk_pct = strat_cfg.get("risk_pct", 0.5) / 100.0
                    risk_dollars = starting_equity * risk_pct
                    raw_qty = risk_dollars / (or_width * spec.point_value)
                    qty = max(1.0, math.floor(raw_qty)) # Floor to nearest whole contract, minimum 1
                    
                    logging.info(f"[{inst}] OR High: {or_high:.2f} | OR Low: {or_low:.2f} | Width: {or_width:.2f}")
                    logging.info(f"[{inst}] Sizing: {qty} contracts (Risk: ${risk_dollars:.2f})")
                    
                    # Construct the Institutional OCO Bracket
                    tag = f"ORB{or_mins}-{today.strftime('%m%d')}"
                    bracket = OcoBracket(
                        instrument=inst,
                        qty=qty,
                        long_entry=or_high + spec.slippage,
                        long_stop=or_low - spec.slippage,
                        long_target=None, # Runner strategy: exit at flatten time
                        short_entry=or_low - spec.slippage,
                        short_stop=or_high + spec.slippage,
                        short_target=None,
                        tag=tag
                    )
                    
                    # Send to Broker
                    state["oco_ids"] = broker.place_oco(bracket)
                    state["status"] = "OCO_PLACED"
                    state["or_high"] = or_high
                    state["or_low"] = or_low
                    logging.info(f"[{inst}] 🚀 OCO Bracket Deployed to Broker! (Orders: {state['oco_ids']})")

            loop_count += 1
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n")
        logging.info("🛑 KeyboardInterrupt received (Ctrl+C). Initiating graceful shutdown...")
    except Exception as e:
        logging.error(f"❌ Fatal crash in event loop: {e}")
    finally:
        logging.info("Disconnecting from broker...")
        broker.disconnect()
        logging.info("Kestrel Engine shutdown complete. Goodbye!")