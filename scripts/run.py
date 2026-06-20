"""
Kestrel Engine - Live Production Entry Point.
This script loads the configuration and initializes the broker/strategy.
"""
import logging
import argparse
from kestrel.execution.ibkr import IBKRBroker

def make_broker(cfg):
    """Instantiates the correct broker based on the nested YAML config."""
    broker_name = cfg.broker.get("name", "").lower()
    
    if broker_name == "ibkr":
        host = cfg.broker.get("host", "127.0.0.1")
        port = cfg.broker.get("port", 7497)
        client_id = cfg.broker.get("client_id", 1)
        return IBKRBroker(host=host, port=port, client_id=client_id)
        
    elif broker_name == "oanda":
        from kestrel.execution.oanda import OandaBroker
        # Extract the values from the cfg dictionary
        env = cfg.broker.get("env", "practice")
        token = cfg.broker.get("token")
        account_id = cfg.broker.get("account_id")
        
        logging.info(f"Connecting to OANDA ({env}) Account: {account_id}")
        
        # Correctly passing the extracted configuration to the constructor
        return OandaBroker(env=env, account_id=account_id, token=token)
        
    else:
        raise ValueError(f"Unknown broker requested in config: '{broker_name}'")

# --- MAIN EXECUTION LOGIC ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config_path", help="Path to the YAML config file")
    parser.add_argument("--live", action="store_true", help="Run in live execution mode")
    args = parser.parse_args()

    # Note: Logic for loading YAML and running the strategy would follow here.
    # Ensure your config/oanda.yaml contains the 'account_id' field.
    logging.basicConfig(level=logging.INFO)
    print(f"Loading config from: {args.config_path}")