"""Backtest instruments from CSVs.
    python scripts/backtest.py MNQ=/path/MNQ_M1.csv SPY=/path/SPY_M1.csv ..."""
import sys
from pathlib import Path
import pandas as pd
from kestrel.data import load_csv
from kestrel.engine.backtester import backtest
from kestrel.engine.metrics import summarize, by_year, money_sim
from kestrel.instruments import get_spec

def main(argv):
    if not argv: print(__doc__); return
    out = Path("backtest_out"); out.mkdir(exist_ok=True)
    logs = {}; rows = []
    for a in argv:
        k, p = a.split("=", 1)
        spec = get_spec(k)
        # Load each market in ITS OWN session timezone so the opening range aligns
        # to the local cash open (US=NY, DAX=Berlin, FTSE=London, ...), not always NY.
        tr = backtest(load_csv(p, target_tz=spec.session.timezone), spec); logs[k] = tr
        tr.to_csv(out / f"{k}.csv", index=False)
        rows.append({"asset": k, **summarize(tr)})
    print(pd.DataFrame(rows).to_string(index=False))
    if logs:
        allt = pd.concat(logs.values()).sort_values("date")
        _, st = money_sim(allt, 10000, 0.005)
        print(f"\nPortfolio ({'+'.join(logs)}) EUR10k @0.5%: {st}")
        print("\nPer-year (combined):")
        print(by_year(allt).to_string())

if __name__ == "__main__": main(sys.argv[1:])
