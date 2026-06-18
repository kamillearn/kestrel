"""Live/paper entrypoint.
    python scripts/run.py config/config.yaml [--live]   (default = dry-run)"""
import logging, sys
from kestrel.config import load_config
from kestrel.risk.manager import RiskManager
from kestrel.live.runner import Runner

def make_broker(cfg):
    if cfg.broker == "ibkr":
        from kestrel.execution.ibkr import IBKRBroker; return IBKRBroker()
    from kestrel.execution.oanda import OandaBroker; return OandaBroker()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else "config/config.yaml")
    dry = "--live" not in sys.argv
    broker = make_broker(cfg)
    broker.connect(); eq = broker.equity(); broker.disconnect()
    Runner(cfg, broker, RiskManager(cfg.risk, eq), dry_run=dry).start()
