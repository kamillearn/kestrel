"""Turn a backtest trade-log CSV into a performance report."""
from __future__ import annotations
import sys, pandas as pd
from kestrel.engine.metrics import summarize, by_year, money_sim

def report(path, start=10000, risk=0.005):
    tr = pd.read_csv(path)
    print("Summary:", summarize(tr))
    print("\nBy year:\n", by_year(tr).to_string())
    _, st = money_sim(tr, start, risk)
    print(f"\nMoney sim (start {start}, {risk*100:.2f}%/trade): {st}")

if __name__ == "__main__":
    report(sys.argv[1], float(sys.argv[2]) if len(sys.argv)>2 else 10000)
