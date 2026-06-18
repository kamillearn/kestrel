"""Performance metrics + money simulation from an R-based trade log."""
from __future__ import annotations
import numpy as np, pandas as pd

def summarize(tr: pd.DataFrame) -> dict:
    if tr is None or len(tr) == 0: return {"n": 0}
    R = tr["R"].astype(float); w, l = R[R > 0], R[R <= 0]
    eq = R.cumsum(); dd = float((eq.cummax() - eq).max())
    pf = float(w.sum() / abs(l.sum())) if l.sum() != 0 else float("inf")
    return {"n": int(len(R)), "win_pct": round(float((R > 0).mean()*100), 1),
            "expectancy_R": round(float(R.mean()), 3), "total_R": round(float(R.sum()), 1),
            "profit_factor": round(pf, 2), "max_dd_R": round(dd, 1),
            "sharpe_pertrade": round(float(R.mean()/R.std()), 2) if R.std() > 0 else 0.0}

def by_year(tr: pd.DataFrame) -> pd.DataFrame:
    t = tr.copy(); t["year"] = pd.to_datetime(t["date"]).dt.year
    g = t.groupby("year")["R"].agg(count="count", total="sum", mean="mean")
    g["win_pct"] = t.groupby("year")["R"].apply(lambda x: (x > 0).mean()*100)
    return g.round(3)

def money_sim(tr: pd.DataFrame, start: float, risk_pct: float):
    """Compound fixed-fractional risk per trade; returns (equity_series, stats)."""
    t = tr.sort_values("date"); eq = start; peak = start; mdd = 0.0; curve = []
    for _, r in t.iterrows():
        eq += eq * risk_pct * r["R"]; peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak); curve.append(eq)
    s = pd.Series(curve, index=pd.to_datetime(t["date"]).values)
    return s, {"final": round(eq, 2), "max_dd_pct": round(mdd*100, 1), "trades": len(t)}
