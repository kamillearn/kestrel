"""Backtester that simulates the SAME OCO bracket the live runner places:
two stop-entries (long@OR-high, short@OR-low), first-touch wins, protective stop
at the opposite end, optional R-multiple target, square off at the flatten time.

Conventions (conservative):
  * Stop-entry fills at its level + slippage (adverse).
  * If a bar touches both protective stop and target, stop assumed first.
  * Spread (from the entry bar) charged once per round trip.
Results are in R (risk = opening-range width), so they're size/currency agnostic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from kestrel.instruments import InstrumentSpec
from kestrel.strategy.filters import trend_allowed_map
from kestrel.strategy.orb import ORBStrategy


def backtest(df: pd.DataFrame, spec: InstrumentSpec, target_R=None,
             slippage: float | None = None, trend_sma: int | None = None) -> pd.DataFrame:
    s = spec.session
    slip = spec.slippage if slippage is None else slippage
    strat = ORBStrategy(spec, target_R=target_R)
    # Optional daily-trend filter: veto breakouts that disagree with the prior
    # close vs an N-day SMA (and the early days that lack enough history).
    allowed = trend_allowed_map(df, s, trend_sma) if trend_sma else None
    rth = df[(df["et_min"] >= s.open_m) & (df["et_min"] < s.close_m)]
    rows = []
    for day, g in rth.groupby("etdate"):
        plan = strat.build_plan(g, day)
        if plan is None:
            continue
        post = g[g["et_min"] >= s.or_end_m]
        if len(post) < 2:
            continue
        hi = post["high"].values; lo = post["low"].values
        cl = post["close"].values; op = post["open"].values
        sp = post["spread"].values; mins = post["et_min"].values

        up = np.where(hi >= plan.or_high)[0]
        dn = np.where(lo <= plan.or_low)[0]
        iu = up[0] if len(up) else 10**9
        idn = dn[0] if len(dn) else 10**9
        if iu == 10**9 and idn == 10**9:
            continue
        side = "long" if iu < idn else ("short" if idn < iu else
                                        ("long" if cl[iu] >= op[iu] else "short"))
        # trend filter: skip counter-trend breakouts (and pre-history days)
        if allowed is not None and allowed.get(day) != side:
            continue
        ei = min(iu, idn)
        risk = plan.risk
        if side == "long":
            entry = plan.long_entry + slip; stop = plan.long_stop; tgt = plan.long_target()
        else:
            entry = plan.short_entry - slip; stop = plan.short_stop; tgt = plan.short_target()
        spread_e = sp[ei]

        exitp, reason = None, None
        for j in range(ei + 1, len(post)):
            if mins[j] >= s.flatten_m:
                exitp, reason = cl[j], "flatten"; break
            if side == "long":
                if lo[j] <= stop: exitp, reason = stop - slip, "stop"; break
                if tgt is not None and hi[j] >= tgt: exitp, reason = tgt - slip, "target"; break
            else:
                if hi[j] >= stop: exitp, reason = stop + slip, "stop"; break
                if tgt is not None and lo[j] <= tgt: exitp, reason = tgt + slip, "target"; break
        if exitp is None:
            exitp, reason = cl[-1], "eod"
        gross = (exitp - entry) if side == "long" else (entry - exitp)
        net = gross - spread_e
        rows.append({"date": day, "side": side, "entry": round(entry, 2),
                     "exit": round(exitp, 2), "risk": round(risk, 2),
                     "pts": round(net, 2), "R": net / risk if risk > 0 else 0.0,
                     "reason": reason})
    return pd.DataFrame(rows)
