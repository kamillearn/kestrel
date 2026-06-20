"""
snapshot.py — the single read/write contract between Kestrel's live executor and
the read-only monitoring layer (Heartbeat + MCP server).

The live runner calls write_snapshot(...) once per cycle. Everything else only
READS this file (and the journal CSV). Nothing here talks to a broker — that keeps
the monitoring/agent surface incapable of placing orders by construction.
"""
from __future__ import annotations
import os, json, csv, tempfile, datetime as dt
from pathlib import Path

VAR = Path(os.environ.get("KESTREL_VAR", "./var"))
SNAPSHOT = VAR / "state.json"
JOURNAL  = VAR / "journal.csv"
KILL     = VAR / "KILL"          # presence of this file tells the executor to flatten & stand down


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_snapshot(*, equity: float, day_pnl_r: float, day_pnl_ccy: float,
                   open_positions: list[dict], armed: list[dict],
                   data_age_sec: dict[str, float], peak_equity: float | None = None) -> None:
    """Called by the live runner each loop. Atomic write."""
    VAR.mkdir(parents=True, exist_ok=True)
    snap = {
        "updated": _utcnow(),
        "equity": round(equity, 2),
        "peak_equity": round(peak_equity if peak_equity is not None else equity, 2),
        "day_pnl_r": round(day_pnl_r, 3),
        "day_pnl_ccy": round(day_pnl_ccy, 2),
        "open_positions": open_positions,     # [{instrument, side, qty, entry, stop}]
        "armed": armed,                        # [{instrument, or_high, or_low, state}]
        "data_age_sec": data_age_sec,          # {instrument: seconds since last bar}
        "kill": KILL.exists(),
    }
    fd, tmp = tempfile.mkstemp(dir=str(VAR), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(snap, f, indent=2)
    os.replace(tmp, SNAPSHOT)


def read_snapshot() -> dict | None:
    if not SNAPSHOT.exists():
        return None
    with open(SNAPSHOT) as f:
        return json.load(f)


def read_journal(n: int | None = None) -> list[dict]:
    if not JOURNAL.exists():
        return []
    with open(JOURNAL) as f:
        rows = list(csv.DictReader(f))
    return rows[-n:] if n else rows


def snapshot_age_sec(snap: dict) -> float:
    t = dt.datetime.strptime(snap["updated"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - t).total_seconds()


def set_kill(enable: bool) -> bool:
    VAR.mkdir(parents=True, exist_ok=True)
    if enable:
        KILL.write_text(_utcnow())
    elif KILL.exists():
        KILL.unlink()
    return KILL.exists()
