"""Live/paper entrypoint.
    python scripts/run.py config/config.yaml [--live]   (default = dry-run)"""
import logging
import sys
from kestrel.config import load_config
from kestrel.risk.manager import RiskManager
from kestrel.live.runner import Runner

def make_broker(cfg):
    """Instantiates the correct broker based on the nested YAML config."""
    # Check the nested 'name' field from the 'broker' block in config.yaml
    broker_name = cfg.broker.get("name", "").lower()
    
    if broker_name == "ibkr":
        from kestrel.execution.ibkr import IBKRBroker
        
        # Extract host, port, and client_id safely, with fallbacks
        host = cfg.broker.get("host", "127.0.0.1")
        port = cfg.broker.get("port", 7497)
        client_id = cfg.broker.get("client_id", 1)
        
        logging.info(f"Connecting to IBKR Gateway at {host}:{port} (Client ID: {client_id})")
        return IBKRBroker(host=host, port=port, client_id=client_id)
        
    elif broker_name == "oanda":
        from kestrel.execution.oanda import OandaBroker
        return OandaBroker()
        
    else:
        raise ValueError(f"Unknown broker requested in config: '{broker_name}'. Expected 'ibkr' or 'oanda'.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
        
    # Load the YAML configuration
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/config.yaml"
    cfg = load_config(config_path)
    
    # Check for the live flag (defaults to paper/dry-run if omitted)
    dry = "--live" not in sys.argv
    if dry:
        logging.info("Starting Kestrel in DRY-RUN mode. No real orders will be sent.")
    else:
        logging.warning("Starting Kestrel in LIVE execution mode!")

    # Instantiate the broker
    broker = make_broker(cfg)
    
    try:
        # Pre-flight check: Connect and get the true account equity
        broker.connect()
        eq = broker.equity()
        logging.info(f"Pre-flight successful. Starting Equity: ${eq:,.2f}")
        broker.disconnect()
        
        # Launch the main event loop
        Runner(cfg, broker, RiskManager(cfg.risk, eq), dry_run=dry).start()
        
    except Exception as e:
        logging.error(f"Fatal error during startup: {e}")
        sys.exit(1)