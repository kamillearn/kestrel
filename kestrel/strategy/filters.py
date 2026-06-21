"""Trade-quality filters layered on top of the raw ORB edge.

Daily-trend alignment: only take the breakout whose side agrees with the prior
daily close vs an N-day SMA of daily closes. Validated to lift win% AND
expectancy on MNQ/MES, in-sample and out-of-sample, across a broad SMA plateau
(N=15-50). All inputs use data strictly BEFORE the trading day — no lookahead.
"""
from __future__ import annotations

import pandas as pd


def daily_closes(df: pd.DataFrame, session) -> pd.Series:
    """One close per ET date, taken from the last RTH bar of each day."""
    rth = df[(df["et_min"] >= session.open_m) & (df["et_min"] < session.close_m)]
    return rth.groupby("etdate")["close"].last()


def trend_allowed_side(prior_closes: pd.Series | None, n: int) -> str | None:
    """The breakout side the trend permits, from daily closes known BEFORE today
    (most recent last): 'long' if the latest close is above its N-day SMA, else
    'short'. Returns None when fewer than N closes are available."""
    if prior_closes is None or len(prior_closes) < n:
        return None
    last = float(prior_closes.iloc[-1])
    sma = float(prior_closes.tail(n).mean())
    return "long" if last > sma else "short"


def trend_allowed_map(df: pd.DataFrame, session, n: int) -> dict:
    """Per-ET-date allowed side, using only data strictly before each day
    (``shift(1)`` on both the close and the SMA). None until N days of history."""
    dc = daily_closes(df, session)
    ref = dc.shift(1)
    sma = dc.rolling(n).mean().shift(1)
    out = {}
    for d in dc.index:
        r, sm = ref.get(d), sma.get(d)
        out[d] = None if (pd.isna(r) or pd.isna(sm)) else ("long" if r > sm else "short")
    return out
