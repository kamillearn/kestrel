"""
Kestrel Engine - Live Production Entry Point (the single, canonical runner).

The daily lifecycle per instrument is a 5-state machine:

    WAITING_FOR_OPEN -> OCO_PLACED -> IN_TRADE -> FLATTENED
                                   `-> EXPIRED   (decay-cancelled, never filled)

PHASE C (manage) bridges placement and flatten with adaptive behaviour:
  1) fill detection + software-OCO (cancel the resting opposite entry),
  2) time-decay cancellation of un-triggered brackets past a cutoff (default 11:30 ET),
  3) break-even auto-trail: move the stop to entry once the trade is +1R.

Safety: durable state (crash-safe), a RiskManager guarding every placement
(daily-loss / max-trades / max-concurrent / loss-streak circuit breakers + a
`touch KILL` kill-switch file), and dry-run by default (add --live to send orders).
"""
import logging
import argparse
import json
import yaml
import time
from dataclasses import dataclass, asdict, fields
from datetime import datetime
from pathlib import Path
from typing import Optional
import math

from kestrel.execution.ibkr import IBKRBroker
from kestrel.execution.oanda import OandaBroker
from kestrel.execution.broker import OcoBracket, BracketHandle, OrderKind
from kestrel.instruments import SPECS
from kestrel.reporting.journal import Journal
from kestrel.risk.manager import RiskManager, RiskConfig
from kestrel.strategy.filters import trend_allowed_side
from kestrel.utils.sessions import ET

STATE_PATH = "logs/kestrel_state.json"


def et_minute(hhmm) -> int:
    """'11:30' -> 690 (minutes past ET midnight)."""
    h, m = str(hhmm).split(":")
    return int(h) * 60 + int(m)


def broker_symbol(broker, inst):
    """Map a SPEC key (e.g. 'MNQ') to the identifier THIS broker's API expects.
    IBKR trades the futures symbol; OANDA trades the CFD symbol (e.g. NAS100_USD).
    Keeping the SPEC key for state/specs while sending the broker its own symbol is
    what lets the same engine drive both venues."""
    spec = SPECS[inst]
    if isinstance(broker, OandaBroker):
        return spec.oanda_symbol or inst
    return spec.ibkr_symbol or inst


class Status:
    """The 5 states an instrument moves through in a session."""
    WAITING = "WAITING_FOR_OPEN"
    PLACED = "OCO_PLACED"
    IN_TRADE = "IN_TRADE"          # an entry filled
    EXPIRED = "EXPIRED"            # decay-cancelled, never filled
    FLAT = "FLATTENED"


@dataclass
class InstrRuntime:
    """Per-instrument daily state. Serialized to logs/kestrel_state.json after
    every major transition so a restart never loses resting orders or open trades."""
    status: str = Status.WAITING
    # placement
    handle: Optional[BracketHandle] = None   # carries entry + stop order ids
    qty: float = 0.0
    or_high: float = 0.0
    or_low: float = 0.0
    long_entry: float = 0.0
    long_stop: float = 0.0
    short_entry: float = 0.0
    short_stop: float = 0.0
    placed_at: Optional[str] = None
    # fill / management
    side: Optional[str] = None               # "long" | "short"
    entry_price: float = 0.0                  # R-reference (the planned entry level)
    risk_per_unit: float = 0.0                # 1R distance = |entry - stop|
    stop_order_id: Optional[str] = None       # the live stop we may trail
    be_moved: bool = False                    # break-even idempotency
    decay_cancelled: bool = False             # time-decay idempotency
    last_managed_ts: float = 0.0              # wall-clock throttle for the manage poll


# ---------------------------------------------------------------------------
# Durable state — survive a VPS restart without losing track of resting orders
# or open trades. The whole daily_state is serialized to logs/kestrel_state.json
# and only reloaded if the date inside matches today.
# ---------------------------------------------------------------------------

def _from_dict(cls, d):
    """Build a dataclass from a dict, ignoring unknown keys (forward-compatible)."""
    valid = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in valid})


def _instr_from_dict(d):
    d = dict(d)
    h = d.pop("handle", None)
    st = _from_dict(InstrRuntime, d)
    st.handle = _from_dict(BracketHandle, h) if h else None
    return st


def save_state(daily_state, date_str, path=STATE_PATH):
    """Atomically persist the full daily_state after a major transition."""
    payload = {"date": date_str,
               "instruments": {k: asdict(v) for k, v in daily_state.items()}}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    tmp.replace(p)   # atomic swap — a crash mid-write never corrupts the live file


def load_state(today_str, instruments, path=STATE_PATH):
    """Reload daily_state ONLY if the file's date matches today; else None."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logging.warning(f"State file {path} unreadable ({e}); starting fresh.")
        return None
    if raw.get("date") != today_str:
        return None
    saved = raw.get("instruments", {})
    return {inst: (_instr_from_dict(saved[inst]) if saved.get(inst) else InstrRuntime())
            for inst in instruments}


def make_broker(cfg):
    """Instantiates the correct broker based on the nested YAML config."""
    broker_name = cfg.get("broker", {}).get("name", "").lower()

    if broker_name == "ibkr":
        host = cfg["broker"].get("host", "127.0.0.1")
        port = cfg["broker"].get("port", 7497)
        client_id = cfg["broker"].get("client_id", 1)
        return IBKRBroker(host=host, port=port, client_id=client_id)

    elif broker_name == "oanda":
        broker_cfg = cfg.get("broker", {})
        env = broker_cfg.get("env", "practice")
        token = broker_cfg.get("token")
        account_id = broker_cfg.get("account_id")
        return OandaBroker(env=env, account_id=account_id, token=token)
    else:
        raise ValueError(f"Unknown broker requested in config: '{broker_name}'")


def make_risk(cfg, equity, n_instruments):
    """Build the RiskManager (circuit breakers + kill-switch) from config, with
    portfolio-aware defaults so a multi-instrument book isn't throttled by accident."""
    rc = cfg.get("risk", {})
    return RiskManager(RiskConfig(
        risk_per_trade=rc.get("risk_per_trade", 0.005),
        max_daily_loss=rc.get("max_daily_loss", 0.02),
        max_trades_per_day=rc.get("max_trades_per_day", max(3, n_instruments)),
        max_concurrent=rc.get("max_concurrent", max(2, n_instruments)),
        max_consecutive_losses=rc.get("max_consecutive_losses", 6),
        kill_switch_file=rc.get("kill_switch_file", "KILL"),
    ), equity)


def get_strategy_config(cfg, instrument_key):
    """Extracts specific strategy overrides for an instrument from the config."""
    assets = cfg.get("strategy", {}).get("assets", [])
    for asset in assets:
        if asset.get("key") == instrument_key:
            return asset
    return {}


def _est_pnl(st: "InstrRuntime", spec, exit_px) -> float:
    """Best-effort realized PnL for the risk breakers. The exit is proxied by the
    last price (the known live-accounting gap), so the SIGN is reliable but the
    magnitude is approximate; returns 0 when no fill ever happened."""
    if st.side is None or st.qty <= 0 or exit_px != exit_px:   # no fill / NaN
        return 0.0
    pts = (exit_px - st.entry_price) if st.side == "long" else (st.entry_price - exit_px)
    return pts * spec.point_value * st.qty


def manage(broker, inst, st: InstrRuntime, spec, now_m, decay_cancel_m, breakeven_R,
           risk, journal):
    """PHASE C — the adaptive bridge between placement and flatten. Live only."""
    sym = broker_symbol(broker, inst)
    pos = [p for p in broker.positions() if p.instrument == sym]

    # 1) FILL DETECTION  (PLACED -> IN_TRADE)  + software-OCO
    if st.status == Status.PLACED and pos:
        st.side = pos[0].side
        st.status = Status.IN_TRADE
        if st.side == "long":
            st.entry_price, stop = st.long_entry, st.long_stop
        else:
            st.entry_price, stop = st.short_entry, st.short_stop
        st.risk_per_unit = abs(st.entry_price - stop)
        st.stop_order_id = st.handle.stop_id_for(st.side) if st.handle else None
        # OANDA creates the protective stop server-side on fill, so the handle
        # carries no stop id at placement — discover the trade's child stop now.
        if st.stop_order_id is None:
            try:
                stops = [w for w in broker.working_orders(sym)
                         if w.kind == OrderKind.STOP and w.side in (st.side, "")]
                if stops:
                    st.stop_order_id = stops[0].id
            except NotImplementedError:
                pass
        # cancel the resting opposite entry: a no-op under IBKR's native OCA,
        # but REQUIRED on venues without one (e.g. OANDA).
        opp = st.handle.entry_id_for("short" if st.side == "long" else "long") if st.handle else None
        if opp:
            broker.cancel_order(sym, opp)
        logging.info(f"[{inst}] ✅ Filled {st.side.upper()} @~{st.entry_price:.2f} "
                     f"(1R={st.risk_per_unit:.2f}). Opposite cancelled — now IN_TRADE.")
        return

    # 2) TIME-DECAY CANCEL  (still PLACED, no fill, past the cutoff)  -> EXPIRED
    if st.status == Status.PLACED and not pos and now_m >= decay_cancel_m and not st.decay_cancelled:
        for oid in (st.handle.entry_ids if st.handle else []):
            broker.cancel_order(sym, oid)      # entries only; nothing else is live
        st.decay_cancelled = True
        st.status = Status.EXPIRED
        logging.info(f"[{inst}] ⌛ No breakout by {decay_cancel_m // 60:02d}:{decay_cancel_m % 60:02d} ET. "
                     f"Cancelled resting entries (EXPIRED).")
        return

    # position closed while we were IN_TRADE (stop hit / target / manual) -> done
    if st.status == Status.IN_TRADE and not pos:
        st.status = Status.FLAT
        risk.on_close(_est_pnl(st, spec, broker.last_price(sym)))
        journal.record_day(datetime.now(ET).date(), inst, st.side, broker.equity())
        logging.info(f"[{inst}] Position closed (stop/target). Done for the day.")
        return

    # 3) BREAK-EVEN TRAIL  (IN_TRADE, hit +1R, not yet moved)
    if st.status == Status.IN_TRADE and not st.be_moved and st.stop_order_id:
        px = broker.last_price(sym)
        if px != px:                            # NaN guard (no price yet)
            return
        if st.side == "long":
            hit = px >= st.entry_price + breakeven_R * st.risk_per_unit
        else:
            hit = px <= st.entry_price - breakeven_R * st.risk_per_unit
        if hit:
            # move the stop to entry — modify-in-place, no unprotected window
            st.stop_order_id = broker.modify_stop(sym, st.stop_order_id, st.entry_price)
            st.be_moved = True
            logging.info(f"[{inst}] 🛡️ +{breakeven_R:g}R reached (px={px:.2f}). "
                         f"Stop trailed to break-even @ {st.entry_price:.2f}.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config_path", help="Path to the YAML config file")
    parser.add_argument("--live", action="store_true", help="Run in live execution mode")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    logging.getLogger('ib_insync').setLevel(logging.WARNING)  # silence verbose IBKR logs

    logging.info(f"Loading config from: {args.config_path}")
    with open(args.config_path, 'r') as f:
        cfg = yaml.safe_load(f)

    broker = make_broker(cfg)
    broker.connect()

    starting_equity = broker.equity()
    logging.info(f"Pre-flight successful. Live Equity: ${starting_equity:,.2f}")

    instruments_to_watch = cfg.get("instruments", [])
    logging.info(f"Loaded Portfolio: {instruments_to_watch}")

    risk = make_risk(cfg, starting_equity, len(instruments_to_watch))
    journal_path = cfg.get("engine", {}).get("journal_path", "logs/journal.csv")
    Path(journal_path).parent.mkdir(parents=True, exist_ok=True)  # Journal writes on init
    journal = Journal(journal_path)
    logging.info(f"Risk guards armed: kill-switch='{risk.cfg.kill_switch_file}', "
                 f"max_trades/day={risk.cfg.max_trades_per_day}, "
                 f"max_concurrent={risk.cfg.max_concurrent}, "
                 f"daily_loss={risk.cfg.max_daily_loss:.1%}")

    # How often PHASE C polls the broker (REST-friendly for OANDA; IBKR is a socket).
    manage_poll_s = cfg.get("engine", {}).get("manage_poll_seconds", 10)

    current_date = None
    daily_state = {}

    logging.info("🧠 Kestrel Engine wired. Entering continuous execution loop.")
    loop_count = 0

    try:
        while True:
            # 1. TIME ALIGNMENT (Always use Eastern Time for the Wall Street clock)
            now_et = datetime.now(ET)
            today = now_et.date()
            now_m = now_et.hour * 60 + now_et.minute

            # --- ADD THIS WEEKEND CHECK ---
            if today.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
                time.sleep(300)       # Sleep for 5 minutes at a time
                continue

            # 2. DAILY STATE RESET (If a new day has started)
            if current_date != today:
                current_date = today
                risk.roll_day(today)
                resumed = load_state(today.isoformat(), instruments_to_watch)
                if resumed is not None:
                    daily_state = resumed
                    logging.info(f"♻️  Resumed durable state from {STATE_PATH} for {current_date}.")
                else:
                    daily_state = {inst: InstrRuntime() for inst in instruments_to_watch}
                    logging.info(f"🌅 New Trading Session Started: {current_date}")

            # 3. ITERATE THROUGH PORTFOLIO
            for inst in instruments_to_watch:
                st = daily_state[inst]
                if st.status == Status.FLAT:
                    continue  # Done for the day

                spec = SPECS[inst]
                sym = broker_symbol(broker, inst)
                strat_cfg = get_strategy_config(cfg, inst)

                # Use custom OR minutes from config if provided, else registry default
                or_mins = strat_cfg.get("or_minutes", spec.session.or_minutes)

                # Session times + adaptive knobs
                open_m = spec.session.open_m
                or_end_m = open_m + or_mins
                flatten_m = spec.session.flatten_m
                decay_cancel_m = et_minute(strat_cfg.get("decay_cancel_et", "11:30"))
                breakeven_R = strat_cfg.get("breakeven_R", 1.0)

                # PHASE A: FLATTEN (End of Day)
                if now_m >= flatten_m and st.status != Status.FLAT:
                    logging.info(f"[{inst}] 🔔 End of Session reached. Flattening all positions and orders.")
                    if args.live:
                        exit_px = broker.last_price(sym) if st.side else float("nan")
                        broker.cancel_all(sym)
                        broker.flatten(sym)
                        risk.on_close(_est_pnl(st, spec, exit_px))  # balances on_open; 0 if no fill
                        if st.side:
                            journal.record_day(today, inst, st.side, broker.equity())
                    else:
                        logging.info(f"[{inst}] DRY RUN: Would cancel_all + flatten.")
                    st.status = Status.FLAT
                    save_state(daily_state, today.isoformat())
                    continue

                # PHASE B: OPENING RANGE BREAKOUT (ORB) CALCULATION & EXECUTION
                if now_m >= or_end_m and st.status == Status.WAITING:
                    if not args.live:
                        logging.info(f"[{inst}] DRY RUN: Would execute {or_mins}-min Opening Range breakout here.")
                        st.status = Status.PLACED
                        continue

                    # Circuit breakers + kill-switch gate every live placement
                    ok, why = risk.can_trade()
                    if not ok:
                        logging.warning(f"[{inst}] ⛔ Placement gated by risk manager: {why}.")
                        continue   # re-evaluated next tick (kill removed / limits roll over)

                    logging.info(f"[{inst}] ⏳ Opening Range ({or_mins}m) completed. Calculating edge...")

                    # Fetch recent bars to find the High/Low of the opening range
                    bars = broker.recent_bars(sym, count=60)
                    if bars.empty:
                        logging.error(f"[{inst}] Failed to fetch recent bars. Retrying next loop.")
                        continue

                    # Filter bars strictly to the Opening Range period for today
                    bars["et_time"] = bars.index.tz_convert(ET)
                    or_bars = bars[
                        (bars["et_time"].dt.date == today) &
                        (bars["et_time"].dt.hour * 60 + bars["et_time"].dt.minute >= open_m) &
                        (bars["et_time"].dt.hour * 60 + bars["et_time"].dt.minute < or_end_m)
                    ]
                    if or_bars.empty:
                        # Throttle the warning so it only prints once per minute instead of once per second
                        if gate_logged_m.get(f"{inst}_nodata") != now_m:
                            logging.warning(f"[{inst}] No data found for the Opening Range period today (Market Holiday?).")
                            gate_logged_m[f"{inst}_nodata"] = now_m
                        continue

                    or_high = float(or_bars["high"].max())
                    or_low = float(or_bars["low"].min())
                    or_width = or_high - or_low
                    if or_width <= 0:
                        continue  # Bad data protection

                    # Sizing Math: Risk Amount / (OR Width * Point Value)
                    risk_pct = strat_cfg.get("risk_pct", 0.5) / 100.0
                    risk_dollars = starting_equity * risk_pct
                    raw_qty = risk_dollars / (or_width * spec.point_value)
                    qty = max(1.0, math.floor(raw_qty))  # Floor to whole contract, minimum 1

                    logging.info(f"[{inst}] OR High: {or_high:.2f} | OR Low: {or_low:.2f} | Width: {or_width:.2f}")
                    logging.info(f"[{inst}] Sizing: {qty} contracts (Risk: ${risk_dollars:.2f})")

                    # Daily-trend filter: if enabled, only place the side that agrees
                    # with the prior close vs an N-day SMA (skip counter-trend breakouts).
                    # Fail-safe: any problem falls back to the full two-sided OCO.
                    allowed_sides = ("long", "short")
                    if strat_cfg.get("trend_filter", False):
                        n = int(strat_cfg.get("trend_sma", 20))
                        try:
                            closes = broker.recent_daily_closes(sym, n)
                            side = trend_allowed_side(closes, n)
                            if side is None:
                                logging.warning(f"[{inst}] trend filter: <{n} daily closes available; placing two-sided.")
                            else:
                                allowed_sides = (side,)
                                logging.info(f"[{inst}] 🧭 Trend filter: {side.upper()}-only (prior close vs SMA{n}).")
                        except NotImplementedError:
                            logging.warning(f"[{inst}] trend filter enabled but {type(broker).__name__} "
                                            f"has no daily closes; placing two-sided.")

                    # Construct the OCO bracket (use the broker's own symbol)
                    tag = f"ORB{or_mins}-{today.strftime('%m%d')}"
                    bracket = OcoBracket(
                        instrument=sym,
                        qty=qty,
                        long_entry=or_high + spec.slippage,
                        long_stop=or_low - spec.slippage,
                        long_target=None,  # Runner strategy: exit at flatten time
                        short_entry=or_low - spec.slippage,
                        short_stop=or_high + spec.slippage,
                        short_target=None,
                        tag=tag,
                        allowed_sides=allowed_sides,
                    )

                    # Send to Broker and record everything PHASE C will need
                    st.handle = broker.place_oco(bracket)
                    st.qty = qty
                    st.or_high = or_high
                    st.or_low = or_low
                    st.long_entry = bracket.long_entry
                    st.long_stop = bracket.long_stop
                    st.short_entry = bracket.short_entry
                    st.short_stop = bracket.short_stop
                    st.placed_at = now_et.isoformat()
                    st.status = Status.PLACED
                    risk.on_open()
                    save_state(daily_state, today.isoformat())
                    logging.info(f"[{inst}] 🚀 OCO Bracket Deployed to Broker! (handle: {st.handle})")
                    continue  # manage from the next tick

                # PHASE C: MANAGE (fill detection, time-decay cancel, break-even trail)
                if st.status in (Status.PLACED, Status.IN_TRADE) and args.live:
                    if time.time() - st.last_managed_ts >= manage_poll_s:
                        st.last_managed_ts = time.time()
                        before = (st.status, st.be_moved)
                        manage(broker, inst, st, spec, now_m, decay_cancel_m, breakeven_R,
                               risk, journal)
                        if (st.status, st.be_moved) != before:
                            # persist IN_TRADE / EXPIRED / FLATTENED / be_moved transitions
                            save_state(daily_state, today.isoformat())

            loop_count += 1
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n")
        logging.info("🛑 KeyboardInterrupt received (Ctrl+C). Initiating graceful shutdown...")
    except Exception as e:
        logging.error(f"❌ Fatal crash in event loop: {e}")
    finally:
        logging.info("Disconnecting from broker...")
        broker.disconnect()
        logging.info("Kestrel Engine shutdown complete. Goodbye!")


if __name__ == "__main__":
    main()
