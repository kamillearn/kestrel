"""
heartbeat.py — periodic health check for the Kestrel desk.

Run it on a schedule (cron, systemd timer, or Kestrel's own scheduler). It reads
the snapshot + journal (never the broker) and only raises its voice when something
is actually wrong — quiet when all is well, OpenAlice-style.

    python -m kestrel_ops.heartbeat                # print status, exit 0/1/2
    */5 13-21 * * 1-5  python -m kestrel_ops.heartbeat   # weekday RTH, every 5 min (UTC)

Optional alert sink: set KESTREL_WEBHOOK to POST CRIT/WARN lines somewhere
(Slack/Telegram/Discord incoming webhook). Without it, alerts just log to stderr.
"""
from __future__ import annotations
import os, sys, json, datetime as dt
from urllib import request
from . import snapshot as S

# thresholds (override via env)
MAX_SNAPSHOT_AGE = float(os.environ.get("KESTREL_MAX_SNAPSHOT_AGE", 180))   # sec
MAX_DATA_AGE     = float(os.environ.get("KESTREL_MAX_DATA_AGE", 180))       # sec, during session
DAILY_LOSS_CAP_R = float(os.environ.get("KESTREL_DAILY_LOSS_CAP_R", 4.0))   # R
EQUITY_FLOOR     = float(os.environ.get("KESTREL_EQUITY_FLOOR", 0))         # ccy, 0 = ignore
MAX_DD_PCT       = float(os.environ.get("KESTREL_MAX_DD_PCT", 25.0))        # %

OK, WARN, CRIT = "OK", "WARN", "CRIT"


def _market_open_now() -> bool:
    # crude RTH gate in UTC: Mon-Fri ~13:30-20:00 UTC (US cash). Tune for your book.
    now = dt.datetime.now(dt.timezone.utc)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 13 * 60 + 30 <= minutes <= 20 * 60


def run_checks() -> tuple[str, list[tuple[str, str]]]:
    out: list[tuple[str, str]] = []
    snap = S.read_snapshot()
    if snap is None:
        return CRIT, [(CRIT, "no snapshot found — is the live runner up?")]

    age = S.snapshot_age_sec(snap)
    if age > MAX_SNAPSHOT_AGE:
        out.append((CRIT, f"snapshot stale ({age:.0f}s > {MAX_SNAPSHOT_AGE:.0f}s) — runner may be hung"))

    if snap.get("kill"):
        out.append((WARN, "KILL switch is engaged — desk is standing down"))

    if _market_open_now():
        for inst, a in (snap.get("data_age_sec") or {}).items():
            if a > MAX_DATA_AGE:
                out.append((CRIT, f"{inst} data stale ({a:.0f}s) during session — feed problem"))

    dl = snap.get("day_pnl_r", 0.0)
    if dl <= -DAILY_LOSS_CAP_R:
        out.append((CRIT, f"daily loss {dl:+.2f}R at/over cap (-{DAILY_LOSS_CAP_R:.1f}R)"))
    elif dl <= -DAILY_LOSS_CAP_R * 0.75:
        out.append((WARN, f"daily loss {dl:+.2f}R approaching cap"))

    eq, peak = snap.get("equity", 0.0), snap.get("peak_equity", 0.0)
    if EQUITY_FLOOR and eq < EQUITY_FLOOR:
        out.append((CRIT, f"equity {eq:,.0f} below floor {EQUITY_FLOOR:,.0f}"))
    if peak > 0:
        dd = (peak - eq) / peak * 100
        if dd > MAX_DD_PCT:
            out.append((WARN, f"drawdown {dd:.1f}% over limit {MAX_DD_PCT:.0f}%"))

    if _market_open_now() and not snap.get("armed"):
        out.append((WARN, "session open but nothing armed — check instrument config"))

    level = CRIT if any(l == CRIT for l, _ in out) else WARN if out else OK
    if level == OK:
        out.append((OK, f"healthy — equity {eq:,.0f}, day {dl:+.2f}R, "
                        f"{len(snap.get('open_positions', []))} open, {len(snap.get('armed', []))} armed"))
    return level, out


def _notify(level: str, lines: list[str]) -> None:
    hook = os.environ.get("KESTREL_WEBHOOK")
    if not hook or level == OK:
        return
    try:
        body = json.dumps({"text": f"[Kestrel {level}] " + " | ".join(lines)}).encode()
        request.urlopen(request.Request(hook, data=body,
                        headers={"Content-Type": "application/json"}), timeout=10)
    except Exception as e:  # never let alerting crash the check
        print(f"(webhook failed: {e})", file=sys.stderr)


def main() -> int:
    level, items = run_checks()
    for lvl, msg in items:
        print(f"[{lvl}] {msg}", file=sys.stderr)
    _notify(level, [m for l, m in items if l != OK])
    return {OK: 0, WARN: 1, CRIT: 2}[level]


if __name__ == "__main__":
    raise SystemExit(main())
