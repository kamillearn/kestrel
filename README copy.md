# Daybreak

**An opening-range breakout execution bot for stock-index futures.**

*Daybreak* — the market open the entire edge keys off, and the **break** of the
opening range it trades. One strategy, done properly.

> ⚠️ **Not financial advice.** Results are in-sample backtests under modelled
> costs; the live edge depends on fill quality and is decaying over time. Run it
> in dry-run, then a broker paper account, before risking a cent.

---

## Why this exists

We tested four social-media strategies. Three had no edge. One did — the
opening-range breakout — and only on **index futures**, strongest on the
**Nasdaq (MNQ)**. Daybreak productionises that single validated edge with the
plumbing a systematic desk would insist on: resting OCO orders, persistent state,
broker reconciliation, circuit breakers, a kill switch, and a validation harness
you must pass before adding any instrument.

## The strategy (validated)

At 09:30 ET, take the high/low of the first **30 minutes**. Place a **buy-stop at
the high** (protective stop at the low) and a **sell-stop at the low** (protective
stop at the high) as an **OCO pair** — first to trigger wins, the other cancels.
No fixed target (exit at the **15:55 flatten** tested best). One trade per day.
Risk = the opening-range width; size so a stop-out costs a fixed % of equity.

**Backtest (this engine, modelled costs = spread + slippage). R = risk per
trade = the opening-range width.**

Per-asset, full sample:

| Asset | Trades | Win% | Expectancy | Total R | PF | Max DD |
|---|---|---|---|---|---|---|
| **MNQ** (micro Nasdaq) | 675 | 49.5% | **+0.213 R** | +143.8 | **1.50** | 17.7 R |
| SPY (S&P ETF) | 1,125 | 43.4% | +0.091 R | +102.2 | 1.18 | 20.1 R |
| MYM (micro Dow) | 631 | 46.0% | +0.034 R | +21.2 | 1.08 | 32.8 R |

Total R **by year, per asset** (trades in brackets; "—" = no data that year —
the micro futures start in 2023):

| Year | MNQ | SPY | MYM |
|---|---|---|---|
| 2021 | — | +2.4 (11) | — |
| 2022 | — | +36.3 (250) | — |
| 2023 | +60.6 (60) | +25.5 (250) | −3.9 (19) |
| 2024 | +51.5 (252) | +13.6 (252) | −13.9 (250) |
| 2025 | +22.2 (250) | +19.8 (250) | +23.3 (248) |
| 2026\* | +9.4 (113) | +4.6 (112) | +15.6 (114) |

Read this honestly: **MNQ carries the book** and is positive every year it trades
(2023's +1.0R/trade is a 60-trade small-sample outlier — don't anchor on it). SPY
is steady and positive every year. **MYM actually lost in 2023–24** and only turned
positive later — it's the marginal member, kept for diversification, not edge.

### €10,000 account — all three assets, 0.5% risk/trade, compounded

| Year | Trades | Total R | Start € | End € | Return |
|---|---|---|---|---|---|
| 2021 | 11 | +2.4 | 10,000 | 10,120 | +1.2% |
| 2022 | 250 | +36.3 | 10,120 | 12,056 | +19.1% |
| 2023 | 329 | +82.3 | 12,056 | 17,743 | +47.2% |
| 2024 | 754 | +51.2 | 17,743 | 22,521 | +26.9% |
| 2025 | 748 | +65.3 | 22,521 | 30,745 | +36.5% |
| 2026\* | 339 | +29.7 | 30,745 | **35,509** | +15.5% |
| **Total** | **2,431** | **+267.2** | **10,000** | **€35,509** | **+255%** |

Max drawdown **17.6%**. \*2026 is a partial year (~5.5 months).

**Validation (MNQ):** broad OR-length plateau (20–40m), survives 8× base slippage
(+90R), walk-forward holds (+31.6R out-of-sample 2025–26), t-stat 2.91. Single
stocks (AAPL/NVDA) were tested and **fail** — excluded by design.

> These figures are an **optimistic ceiling**: in-sample, and assuming fills *at*
> the breakout level. Real breakout fills are worse — verifying that gap on a paper
> account is the single most important pre-live step. Note the visible decay
> (annual returns trending down), so plan for less going forward, not more.

## Architecture

```
daybreak/
├── daybreak/
│   ├── instruments.py        # specs: tick, point value, slippage, broker symbols
│   ├── strategy/orb.py       # builds the day's OCO plan from the opening range
│   ├── engine/
│   │   ├── backtester.py     # simulates the SAME OCO bracket the runner places
│   │   └── metrics.py        # R stats, by-year, money simulation
│   ├── risk/manager.py       # sizing + circuit breakers + kill switch
│   ├── execution/
│   │   ├── broker.py         # venue-agnostic ABC (OcoBracket)
│   │   ├── ibkr.py           # IBKR (ib_insync) — primary, micro futures
│   │   ├── oanda.py          # OANDA (v20) — CFDs / practice
│   │   └── paper.py          # simulated fills for replay/tests
│   ├── live/
│   │   ├── scheduler.py      # session phase clock (ET, DST-safe)
│   │   ├── state.py          # durable daily state + reconciliation (idempotent)
│   │   └── runner.py         # lifecycle: place OCO -> software-OCO -> flatten
│   └── reporting/            # trade journal + performance report
├── scripts/{backtest,validate,run,report}.py
├── config/config.example.yaml
├── deploy/{daybreak.service,run.sh}   ·   Dockerfile   ·   tests/
```

The backtester and the live runner share the **same plan and execution
semantics**, so what you test is what you trade.

## Professional safeguards

- **Resting OCO orders**, not tick-watching — the bot places one bracket at the
  open-range completion and lets the exchange work it.
- **Idempotent persistent state** (`state.json`): a crash/restart mid-session
  never double-places or re-enters.
- **Broker reconciliation** every loop — positions/orders are the source of truth.
- **Circuit breakers**: daily-loss limit, max trades/day, max concurrent,
  consecutive-loss halt.
- **Kill switch**: `touch KILL` on the VPS halts all new orders immediately.
- **Dry-run by default** (`scripts/run.py` logs intended orders; add `--live`).
- **Validation harness** (`scripts/validate.py`): walk-forward + slippage sweep +
  OR-stability + bootstrap drawdown. Don't deploy an instrument that fails it.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml
cp .env.example .env                     # broker creds

# 1) backtest + validate before trusting anything
python scripts/backtest.py MNQ=/path/MNQ_M1.csv SPY=/path/SPY_M1.csv MYM=/path/MYM_M1.csv
python scripts/validate.py MNQ=/path/MNQ_M1.csv

# 2) dry-run live (connects for data, logs orders, sends nothing)
python scripts/run.py config/config.yaml

# 3) go live (IBKR paper first!): start IB Gateway, then
python scripts/run.py config/config.yaml --live

pytest -q
```

## Deploy on your VPS

```bash
# Docker
docker build -t daybreak . && docker run --env-file .env daybreak   # dry-run

# or systemd
sudo cp -r . /opt/daybreak && cp deploy/daybreak.service /etc/systemd/system/
sudo systemctl enable --now daybreak
```

**Broker:** IBKR is the primary venue — trade **micro futures (MNQ/MYM)** for the
real order book and low costs that the edge depends on (futures roughly doubled the
edge vs CFDs in testing). OANDA is wired as a secondary/practice venue. IB Gateway
ports: 4002 paper, 4001 live.

## Before going live (the checklist)

1. **Dry-run for a week** — compare logged plans to what actually happened.
2. **IBKR paper account** for ≥1 month — reconcile *fills* to the backtest model;
   the breakout fill is the #1 unknown.
3. **Start at 1 micro contract**, MNQ only, smallest risk.
4. **Re-run `validate.py` quarterly** — the edge decays; watch it.

### Known production gaps (be aware)
- Real-time fill/PnL accounting in `risk.on_close` is wired through the journal at
  flatten; tighten per-fill PnL capture against broker executions for live.
- Add reconnection/backoff to the broker adapters for 24/5 robustness.
- The `reconcile`/software-OCO is bar-poll based; for second-level precision use
  the broker's native OCA group (IBKR adapter already sets `ocaGroup`).
