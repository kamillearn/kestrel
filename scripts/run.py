"""
Kestrel Engine - Live Production Entry Point.
This script loads the configuration and initializes the broker/strategy.
"""
import logging
import argparse
import yaml
from kestrel.execution.ibkr import IBKRBroker
from kestrel.execution.oanda import OandaBroker

def make_broker(cfg):
    """Instantiates the correct broker based on the nested YAML config."""
    broker_name = cfg.get("broker", {}).get("name", "").lower()
    
    if broker_name == "ibkr":
        host = cfg["broker"].get("host", "127.0.0.1")
        port = cfg["broker"].get("port", 7497)
        client_id = cfg["broker"].get("client_id", 1)
        return IBKRBroker(host=host, port=port, client_id=client_id)
        
    elif broker_name == "oanda":
        # Extract from config dictionary
        broker_cfg = cfg.get("broker", {})
        env = broker_cfg.get("env", "practice")
        token = broker_cfg.get("token")
        account_id = broker_cfg.get("account_id")
        
        # Explicitly pass the arguments
        return OandaBroker(env=env, account_id=account_id, token=token)
        
    else:
        raise ValueError(f"Unknown broker requested in config: '{broker_name}'")

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

    # 1. Initialize the broker
    broker = make_broker(cfg)
    
    # 2. Connect to the API
    broker.connect()
    
    # 3. Fetch Equity to verify it works
    eq = broker.equity()
    
    logging.info(f"Pre-flight successful. Live Equity: ${eq:,.2f}")
    logging.info("Kestrel Engine is ready for execution.")