"""
Kestrel Engine - Live Production Entry Point.
This script loads the configuration and initializes the broker/strategy.

The daily lifecycle per instrument is a 5-state machine:

    WAITING_FOR_OPEN -> OCO_PLACED -> IN_TRADE -> FLATTENED
                                   `-> EXPIRED   (decay-cancelled, never filled)

PHASE C (manage) bridges placement and flatten with adaptive behaviour:
  1) fill detection + software-OCO (cancel the resting opposite entry),
  2) time-decay cancellation of un-triggered brackets past a cutoff (default 11:30 ET),
  3) break-even auto-trail: move the stop to entry once the trade is +1R.
"""
import logging
import argparse
import yaml
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import math

from kestrel.execution.ibkr import IBKRBroker
from kestrel.execution.oanda import OandaBroker
from kestrel.execution.broker import OcoBracket, BracketHandle
from kestrel.instruments import SPECS
from kestrel.utils.sessions import ET


def et_minute(hhmm) -> int:
    """'11:30' -> 690 (minutes past ET midnight)."""
    h, m = str(hhmm).split(":")
    return int(h) * 60 + int(m)


class Status:
    """The 5 states an instrument moves through in a session."""
    WAITING = "WAITING_FOR_OPEN"
    PLACED = "OCO_PLACED"
    IN_TRADE = "IN_TRADE"          # an entry filled
    EXPIRED = "EXPIRED"            # decay-cancelled, never filled
    FLAT = "FLATTENED"


@dataclass
class InstrRuntime:
    """Per-instrument daily state. Serializable so it can later be persisted to a
    durable StateStore for crash-safety (currently in-memory)."""
    status: str = Status.WAITING
    # placement
    handle: Optional[BracketHandle] = None   # carries entry + stop order ids
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


def get_strategy_config(cfg, instrument_key):
    """Extracts specific strategy overrides for an instrument from the config."""
    assets = cfg.get("strategy", {}).get("assets", [])
    for asset in assets:
        if asset.get("key") == instrument_key:
            return asset
    return {}


def manage(broker, inst, st: InstrRuntime, spec, now_m, decay_cancel_m, breakeven_R):
    """PHASE C — the adaptive bridge between placement and flatten. Live only."""
    pos = [p for p in broker.positions() if p.instrument == inst]

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
        # cancel the resting opposite entry: a no-op under IBKR's native OCA,
        # but REQUIRED on venues without one (e.g. OANDA).
        opp = st.handle.entry_id_for("short" if st.side == "long" else "long") if st.handle else None
        if opp:
            broker.cancel_order(inst, opp)
        logging.info(f"[{inst}] ✅ Filled {st.side.upper()} @~{st.entry_price:.2f} "
                     f"(1R={st.risk_per_unit:.2f}). Opposite cancelled — now IN_TRADE.")
        return

    # 2) TIME-DECAY CANCEL  (still PLACED, no fill, past the cutoff)  -> EXPIRED
    if st.status == Status.PLACED and not pos and now_m >= decay_cancel_m and not st.decay_cancelled:
        for oid in (st.handle.entry_ids if st.handle else []):
            broker.cancel_order(inst, oid)     # entries only; nothing else is live
        st.decay_cancelled = True
        st.status = Status.EXPIRED
        logging.info(f"[{inst}] ⌛ No breakout by {decay_cancel_m // 60:02d}:{decay_cancel_m % 60:02d} ET. "
                     f"Cancelled resting entries (EXPIRED).")
        return

    # position closed while we were IN_TRADE (stop hit / target / manual) -> done
    if st.status == Status.IN_TRADE and not pos:
        st.status = Status.FLAT
        logging.info(f"[{inst}] Position closed (stop/target). Done for the day.")
        return

    # 3) BREAK-EVEN TRAIL  (IN_TRADE, hit +1R, not yet moved)
    if st.status == Status.IN_TRADE and not st.be_moved and st.stop_order_id:
        px = broker.last_price(inst)
        if px != px:                            # NaN guard (no price yet)
            return
        if st.side == "long":
            hit = px >= st.entry_price + breakeven_R * st.risk_per_unit
        else:
            hit = px <= st.entry_price - breakeven_R * st.risk_per_unit
        if hit:
            # move the stop to entry — modify-in-place, no unprotected window
            st.stop_order_id = broker.modify_stop(inst, st.stop_order_id, st.entry_price)
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

    logging.info(f"Loading config from: {args.config_path}")
    with open(args.config_path, 'r') as f:
        cfg = yaml.safe_load(f)

    broker = make_broker(cfg)
    broker.connect()

    starting_equity = broker.equity()
    logging.info(f"Pre-flight successful. Live Equity: ${starting_equity:,.2f}")

    instruments_to_watch = cfg.get("instruments", [])
    logging.info(f"Loaded Portfolio: {instruments_to_watch}")

    # How often PHASE C polls the broker (REST-friendly for OANDA; IBKR is a socket).
    manage_poll_s = cfg.get("engine", {}).get("manage_poll_seconds", 10)

    # Internal Engine State tracker
    current_date = None
    daily_state = {}

    logging.info("🧠 Kestrel Engine Brain Wired. Entering continuous execution loop.")
    loop_count = 0

    try:
        while True:
            # 1. TIME ALIGNMENT (Always use Eastern Time for the Wall Street clock)
            now_et = datetime.now(ET)
            today = now_et.date()
            now_m = now_et.hour * 60 + now_et.minute

            # 2. DAILY STATE RESET (If a new day has started)
            if current_date != today:
                current_date = today
                daily_state = {inst: InstrRuntime() for inst in instruments_to_watch}
                logging.info(f"🌅 New Trading Session Started: {current_date}")

            # 3. ITERATE THROUGH PORTFOLIO
            for inst in instruments_to_watch:
                st = daily_state[inst]
                if st.status == Status.FLAT:
                    continue  # Done for the day

                spec = SPECS[inst]
                strat_cfg = get_strategy_config(cfg, inst)

                # Use custom OR minutes from config if provided, else registry default
                or_mins = strat_cfg.get("or_minutes", spec.session.or_minutes)

                # Session times + new adaptive knobs
                open_m = spec.session.open_m
                or_end_m = open_m + or_mins
                flatten_m = spec.session.flatten_m
                decay_cancel_m = et_minute(strat_cfg.get("decay_cancel_et", "11:30"))
                breakeven_R = strat_cfg.get("breakeven_R", 1.0)

                # PHASE A: FLATTEN (End of Day)
                if now_m >= flatten_m and st.status != Status.FLAT:
                    logging.info(f"[{inst}] 🔔 End of Session reached. Flattening all positions and orders.")
                    if args.live:
                        broker.cancel_all(inst)
                        broker.flatten(inst)
                    else:
                        logging.info(f"[{inst}] DRY RUN: Would cancel_all + flatten.")
                    st.status = Status.FLAT
                    continue

                # PHASE B: OPENING RANGE BREAKOUT (ORB) CALCULATION & EXECUTION
                if now_m >= or_end_m and st.status == Status.WAITING:
                    if not args.live:
                        logging.info(f"[{inst}] DRY RUN: Would execute {or_mins}-min Opening Range breakout here.")
                        st.status = Status.PLACED
                        continue

                    logging.info(f"[{inst}] ⏳ Opening Range ({or_mins}m) completed. Calculating edge...")

                    # Fetch recent bars to find the High/Low of the opening range
                    # We fetch 60 minutes just to be safe, then slice the exact OR window
                    bars = broker.recent_bars(inst, count=60)

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
                        logging.warning(f"[{inst}] No data found for the Opening Range period today.")
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

                    # Construct the Institutional OCO Bracket
                    tag = f"ORB{or_mins}-{today.strftime('%m%d')}"
                    bracket = OcoBracket(
                        instrument=inst,
                        qty=qty,
                        long_entry=or_high + spec.slippage,
                        long_stop=or_low - spec.slippage,
                        long_target=None,  # Runner strategy: exit at flatten time
                        short_entry=or_low - spec.slippage,
                        short_stop=or_high + spec.slippage,
                        short_target=None,
                        tag=tag
                    )

                    # Send to Broker and record everything PHASE C will need
                    st.handle = broker.place_oco(bracket)
                    st.or_high = or_high
                    st.or_low = or_low
                    st.long_entry = bracket.long_entry
                    st.long_stop = bracket.long_stop
                    st.short_entry = bracket.short_entry
                    st.short_stop = bracket.short_stop
                    st.placed_at = now_et.isoformat()
                    st.status = Status.PLACED
                    logging.info(f"[{inst}] 🚀 OCO Bracket Deployed to Broker! (handle: {st.handle})")
                    continue  # manage from the next tick

                # PHASE C: MANAGE (fill detection, time-decay cancel, break-even trail)
                if st.status in (Status.PLACED, Status.IN_TRADE) and args.live:
                    if time.time() - st.last_managed_ts >= manage_poll_s:
                        st.last_managed_ts = time.time()
                        manage(broker, inst, st, spec, now_m, decay_cancel_m, breakeven_R)

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
