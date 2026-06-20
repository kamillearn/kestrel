"""
mcp_server.py — a read-mostly MCP server that lets Claude (Claude Code, Cowork, or
any MCP host) *watch* the Kestrel desk and answer questions from real state.

Design rule: this process reads the snapshot + journal that Kestrel writes. It does
NOT hold broker credentials and CANNOT place, modify, or cancel orders. The only
mutating tool flips the KILL switch (which makes the executor flatten & stand down),
and it requires an explicit confirm=True. Everything else is read-only.

Run:
    pip install "mcp[cli]"
    python -m kestrel_ops.mcp_server          # stdio transport; point your MCP host at it

Then in an MCP-enabled Claude session you can ask things like:
    "Is the desk healthy?"  -> run_heartbeat
    "Did today's fills track the model?" -> fills_vs_model
    "Show me the last 10 trades." -> recent_trades
"""
from __future__ import annotations
from . import snapshot as S
from . import heartbeat as HB

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    raise SystemExit("Install the MCP SDK first:  pip install \"mcp[cli]\"")

mcp = FastMCP("kestrel-desk")


@mcp.tool()
def desk_status() -> dict:
    """Current desk state: equity, day P&L (R and currency), open positions, armed setups, data freshness, kill-switch."""
    snap = S.read_snapshot()
    if snap is None:
        return {"error": "no snapshot — live runner not running or KESTREL_VAR misconfigured"}
    snap["snapshot_age_sec"] = round(S.snapshot_age_sec(snap), 1)
    return snap


@mcp.tool()
def todays_plan() -> dict:
    """The opening ranges / OCO brackets armed for each instrument today, with current state."""
    snap = S.read_snapshot()
    if snap is None:
        return {"error": "no snapshot"}
    return {"updated": snap["updated"], "armed": snap.get("armed", []),
            "open_positions": snap.get("open_positions", [])}


@mcp.tool()
def recent_trades(n: int = 10) -> list[dict]:
    """The last n rows from the trade journal (date, instrument, side, R, fill vs level, etc.)."""
    return S.read_journal(n)


@mcp.tool()
def fills_vs_model(tolerance_ticks: float = 1.0) -> dict:
    """
    Compare today's realized entry fills to the model's expected fill (the OR edge).
    Flags trades where slippage exceeded `tolerance_ticks` — the early-warning that
    live execution is drifting from the backtested edge.
    """
    rows = S.read_journal()
    if not rows:
        return {"note": "journal empty"}
    today = max(r.get("date", "")[:10] for r in rows)
    todays = [r for r in rows if r.get("date", "").startswith(today)]
    flagged = []
    for r in todays:
        try:
            slip = abs(float(r.get("fill", 0)) - float(r.get("level", 0)))
            tick = float(r.get("tick", 1) or 1)
            if slip > tolerance_ticks * tick:
                flagged.append({"instrument": r.get("instrument"), "side": r.get("side"),
                                "slip_ticks": round(slip / tick, 2)})
        except (TypeError, ValueError):
            continue
    return {"date": today, "trades": len(todays), "flagged": flagged,
            "verdict": "fills tracking model" if not flagged else "slippage above tolerance — investigate"}


@mcp.tool()
def run_heartbeat() -> dict:
    """Run the full health check and return level (OK/WARN/CRIT) plus findings."""
    level, items = HB.run_checks()
    return {"level": level, "findings": [{"level": l, "message": m} for l, m in items]}


@mcp.tool()
def set_kill_switch(enable: bool, confirm: bool = False) -> dict:
    """
    GATED WRITE — the only mutating tool. Engages/clears the KILL switch, which makes
    the executor flatten and stand down. It does NOT place orders. Requires confirm=True.
    """
    if not confirm:
        return {"error": "refused: pass confirm=True to change the kill switch",
                "would_set": enable}
    state = S.set_kill(enable)
    return {"kill": state, "note": "executor will flatten & halt" if state else "kill cleared; executor may resume"}


if __name__ == "__main__":
    mcp.run()
