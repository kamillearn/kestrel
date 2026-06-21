"""Validation harness — run this on ANY new instrument before trusting it.

    python scripts/validate.py MNQ=/path/MNQ_M1.csv [SPY=/path/SPY_M1.csv ...]

Produces: per-year breakdown, opening-range stability, slippage sensitivity,
train/test walk-forward, statistical significance and a Monte-Carlo drawdown
distribution. A strategy that fails these is not deployed.
"""
from __future__ import annotations

import sys
import numpy as np
import pandas as pd

from kestrel.data import load_csv
from kestrel.engine.backtester import backtest
from kestrel.engine.metrics import by_year, summarize
from kestrel.instruments import get_spec


def validate_one(key: str, path: str):
    spec = get_spec(key)
    df = load_csv(path, target_tz=spec.session.timezone)  # align OR to the local cash open
    base = backtest(df, spec)
    print(f"\n========== {key} ==========")
    print("baseline:", summarize(base))
    print(by_year(base).to_string())

    print("\nOpening-range stability (total R):")
    print("  " + "  ".join(
        f"{m}m:{backtest(df, spec, target_R=None, slippage=spec.slippage).R.sum():.0f}"
        if m == spec.session.or_minutes else
        f"{m}m:{_or(df, spec, m):.0f}" for m in [10, 15, 20, 25, 30, 40, 45, 60]))

    print("\nSlippage sensitivity (total R):")
    mult = [0, 1, 2, 4, 8]
    print("  " + "  ".join(f"{k}x:{backtest(df, spec, slippage=spec.slippage*k if k else 0.0).R.sum():.0f}"
                           for k in mult) + "   (x = multiples of base slippage)")

    print("\nWalk-forward (train<=2024 -> test>=2025):")
    perf = {}
    for m in [15, 20, 25, 30, 40, 45]:
        tr = _or_log(df, spec, m); y = pd.to_datetime(tr.date).dt.year
        perf[m] = tr[y <= 2024].R.mean() if (y <= 2024).any() else -9
    best = max(perf, key=perf.get)
    tr = _or_log(df, spec, best); y = pd.to_datetime(tr.date).dt.year
    test = tr[y >= 2025]
    print(f"  best OR(train)={best}m -> TEST n={len(test)} exp={test.R.mean():+.3f}R total={test.R.sum():.1f}R")

    R = base.R.values; n = len(R)
    t = R.mean() / (R.std() / np.sqrt(n)) if R.std() > 0 else 0
    rng = np.random.default_rng(1)
    dds = []
    for _ in range(5000):
        s = rng.permutation(R); eq = np.cumsum(s); dds.append((np.maximum.accumulate(eq) - eq).max())
    print(f"\nSignificance: n={n} mean={R.mean():+.3f}R t-stat={t:.2f}")
    print(f"Bootstrap max DD (R) 50/95/99%: "
          f"{np.percentile(dds,50):.1f}/{np.percentile(dds,95):.1f}/{np.percentile(dds,99):.1f}")


def _or(df, spec, m):
    sp = spec.session.__class__(or_minutes=m)
    spec2 = spec.__class__(spec.key, spec.point_value, spec.tick, spec.slippage,
                           spec.contract_step, sp, spec.ibkr_symbol, spec.oanda_symbol)
    return backtest(df, spec2).R.sum()


def _or_log(df, spec, m):
    sp = spec.session.__class__(or_minutes=m)
    spec2 = spec.__class__(spec.key, spec.point_value, spec.tick, spec.slippage,
                           spec.contract_step, sp, spec.ibkr_symbol, spec.oanda_symbol)
    return backtest(df, spec2)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(0)
    for arg in sys.argv[1:]:
        k, p = arg.split("=", 1)
        validate_one(k, p)
