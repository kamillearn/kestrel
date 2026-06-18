"""Daybreak runner — drives the daily lifecycle for each instrument:

  PRE_OPEN/OPENING_RANGE : wait
  ACTIVE  : once (idempotent) build the plan, size it, place the OCO bracket.
            each loop, reconcile: if one side filled, cancel the resting opposite.
  FLATTEN : square off, cancel residual orders, journal the day.

Safety: idempotent via persisted state, per-instrument try/except, kill-switch and
circuit breakers checked before every placement, --dry-run logs instead of sending.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime

from kestrel.execution.broker import OcoBracket
from kestrel.instruments import get_spec
from kestrel.live.scheduler import Phase, phase
from kestrel.live.state import StateStore, reconcile
from kestrel.reporting.journal import Journal
from kestrel.risk.manager import RiskManager
from kestrel.strategy.orb import ORBStrategy
from kestrel.utils.sessions import ET, to_eastern

log = logging.getLogger("daybreak.runner")


class Runner:
    def __init__(self, cfg, broker, risk: RiskManager, dry_run: bool = True,
                 state_path="state.json", journal_path="journal.csv"):
        self.cfg = cfg
        self.broker = broker
        self.risk = risk
        self.dry_run = dry_run
        self.store = StateStore(state_path)
        self.journal = Journal(journal_path)
        self.specs = {s: get_spec(s) for s in cfg.instruments}
        self.strats = {s: ORBStrategy(self.specs[s], target_R=cfg.target_R) for s in cfg.instruments}

    def start(self):
        self.broker.connect()
        log.info("connected (%s); equity=%.2f dry_run=%s",
                 self.cfg.broker, self.broker.equity(), self.dry_run)
        try:
            while True:
                self._tick()
                _time.sleep(self.cfg.poll_seconds)
        finally:
            self.broker.disconnect()

    def _tick(self):
        now = datetime.now(ET)
        today = now.date().isoformat()
        ds = self.store.load(today)
        self.risk.roll_day(now.date())
        for sym in self.cfg.instruments:
            try:
                self._handle(sym, now, ds)
            except Exception:
                log.exception("instrument %s failed this tick", sym)
        self.store.save(ds)

    def _handle(self, sym, now, ds):
        spec = self.specs[sym]
        ph = phase(spec.session, now)
        st = ds.get(sym)
        st = reconcile(self.broker, sym, st)

        # software OCO: if entered, cancel the resting opposite entry
        if st.entered and not st.flattened:
            self.broker.cancel_all(sym)

        if ph == Phase.ACTIVE and not st.plan_placed:
            ok, why = self.risk.can_trade()
            if not ok:
                log.info("%s: skip placement (%s)", sym, why); ds.put(sym, st); return
            bars = to_eastern(self.broker.recent_bars(sym, 800), time_col=None)
            day_bars = bars[bars["etdate"] == now.date()]
            plan = self.strats[sym].build_plan(day_bars, now.date())
            if plan is None:
                log.info("%s: no valid opening range yet", sym); ds.put(sym, st); return
            qty = self.risk.contracts(plan.long_entry, plan.long_stop,
                                      spec.point_value, spec.contract_step)
            if qty <= 0:
                log.info("%s: size rounds to 0", sym); ds.put(sym, st); return
            bracket = OcoBracket(sym, qty, plan.long_entry, plan.long_stop,
                                 plan.short_entry, plan.short_stop,
                                 plan.long_target(), plan.short_target(), tag="daybreak")
            if self.dry_run:
                log.info("[DRY] %s OCO qty=%.0f  L>%.2f(sl %.2f)  S<%.2f(sl %.2f)",
                         sym, qty, plan.long_entry, plan.long_stop,
                         plan.short_entry, plan.short_stop)
                st.order_ids = ["dry"]
            else:
                st.order_ids = self.broker.place_oco(bracket)
                log.info("%s OCO placed ids=%s", sym, st.order_ids)
            st.plan_placed = True
            self.risk.on_open()

        elif ph in (Phase.FLATTEN, Phase.CLOSED) and st.plan_placed and not st.flattened:
            if not self.dry_run:
                self.broker.flatten(sym)
                self.broker.cancel_all(sym)
            st.flattened = True
            log.info("%s flattened/cancelled for the day", sym)
            self.journal.record_day(now.date(), sym, st.side, self.broker.equity())

        ds.put(sym, st)
