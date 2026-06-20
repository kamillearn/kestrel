#!/usr/bin/env python3
"""
fetch_oanda.py — pull 1-minute OANDA v20 candles for the index CFDs that map to
Kestrel's validated edge, and write Kestrel/MT5-style CSVs you can run straight
through scripts/validate.py.

Only fetches instruments worth testing (the index CFDs). FX / metals / crypto
are deliberately omitted — they're already in the debunked pile.

Auth (env vars):
    OANDA_TOKEN   your v20 API token (practice or live)
    OANDA_ENV     "practice" (default) or "live"
    OANDA_ACCOUNT optional, only needed for --list-instruments

Examples:
    # discover the exact instrument codes your account exposes (DAX is DE30_EUR or DE40_EUR)
    OANDA_TOKEN=... OANDA_ACCOUNT=... python fetch_oanda.py --list-instruments

    # fetch the core set since 2021 into ./data/
    OANDA_TOKEN=... python fetch_oanda.py --instruments NAS100_USD DE30_EUR SPX500_USD --from 2021-01-01

Output: data/<INSTRUMENT>_M1.csv with columns  time,open,high,low,close,volume,spread
        time is UTC "YYYY-MM-DD HH:MM:SS"; spread is ask-bid at the bar close, in
        index points (the real OANDA cost — Kestrel uses it as the slippage proxy).
"""
import os, sys, time, csv, argparse, datetime as dt
from urllib import request, parse, error
import json

# OANDA codes that proxy the validated edge. Comment = the futures instrument it maps to.
DEFAULT_SET = [
    "NAS100_USD",   # -> MNQ/NQ  (CORE Nasdaq edge — test this first)
    "DE30_EUR",     # -> DAX     (the non-US winner; CFD lets you size small)
    "SPX500_USD",   # -> SPY/ES  (solid)
    "US2000_USD",   # -> RTY     (borderline; optional)
]
HOSTS = {"practice": "https://api-fxpractice.oanda.com",
         "live":     "https://api-fxtrade.oanda.com"}


def _host():
    return HOSTS[os.environ.get("OANDA_ENV", "practice").lower()]


def _get(path, params=None):
    token = os.environ.get("OANDA_TOKEN")
    if not token:
        sys.exit("ERROR: set OANDA_TOKEN (your v20 API token).")
    url = _host() + path + ("?" + parse.urlencode(params) if params else "")
    req = request.Request(url, headers={"Authorization": f"Bearer {token}",
                                        "Accept-Datetime-Format": "RFC3339"})
    for attempt in range(6):
        try:
            with request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                wait = 2 ** attempt
                print(f"  {e.code}; backing off {wait}s", file=sys.stderr); time.sleep(wait); continue
            sys.exit(f"HTTP {e.code} on {path}: {e.read().decode()[:300]}")
        except error.URLError as e:
            wait = 2 ** attempt
            print(f"  network err ({e.reason}); retry in {wait}s", file=sys.stderr); time.sleep(wait)
    sys.exit(f"giving up on {path} after retries")


def list_instruments():
    acct = os.environ.get("OANDA_ACCOUNT")
    if not acct:
        sys.exit("ERROR: set OANDA_ACCOUNT to list instruments.")
    data = _get(f"/v3/accounts/{acct}/instruments")
    rows = sorted((i["name"], i["displayName"], i["type"]) for i in data["instruments"])
    print(f"{'CODE':<16}{'NAME':<28}TYPE")
    for name, disp, typ in rows:
        flag = "  <-- index" if typ == "CFD" and any(k in name for k in
               ("NAS", "SPX", "US2000", "US30", "DE", "UK", "JP", "EU")) else ""
        print(f"{name:<16}{disp:<28}{typ}{flag}")


def fetch(instrument, start, granularity="M1"):
    out = f"data/{instrument}_M1.csv"
    os.makedirs("data", exist_ok=True)
    cursor = dt.datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    now = dt.datetime.now(dt.timezone.utc)
    n = 0
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "volume", "spread"])
        first = True
        while cursor < now:
            params = {"granularity": granularity, "price": "MBA", "count": 5000,
                      "from": cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "includeFirst": "true" if first else "false"}
            data = _get(f"/v3/instruments/{instrument}/candles", params)
            candles = [c for c in data.get("candles", []) if c.get("complete")]
            if not candles:
                break
            for c in candles:
                t = c["time"][:19].replace("T", " ")          # UTC, second precision
                m, b, a = c["mid"], c["bid"], c["ask"]
                spread = round(float(a["c"]) - float(b["c"]), 5)
                w.writerow([t, m["o"], m["h"], m["l"], m["c"], c["volume"], spread])
            n += len(candles)
            last = dt.datetime.strptime(candles[-1]["time"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
            cursor = last + dt.timedelta(seconds=1)
            first = False
            print(f"  {instrument}: {n:>8,} bars  (through {last:%Y-%m-%d %H:%M})", end="\r", file=sys.stderr)
            time.sleep(0.12)                                   # gentle pacing
    print(f"\n  -> wrote {out}  ({n:,} bars)", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser(description="Fetch OANDA M1 index-CFD candles for Kestrel.")
    ap.add_argument("--instruments", nargs="+", default=DEFAULT_SET,
                    help="OANDA instrument codes (default: the validated index set)")
    ap.add_argument("--from", dest="start", default="2021-01-01", help="start date YYYY-MM-DD")
    ap.add_argument("--granularity", default="M1", help="M1 (default), M5, etc.")
    ap.add_argument("--list-instruments", action="store_true", help="list account instruments and exit")
    args = ap.parse_args()
    if args.list_instruments:
        list_instruments(); return
    print(f"OANDA {os.environ.get('OANDA_ENV','practice')} | {len(args.instruments)} instruments from {args.start}", file=sys.stderr)
    for inst in args.instruments:
        fetch(inst, args.start, args.granularity)
    print("done. Next: PYTHONPATH=. python scripts/validate.py NAS100=data/NAS100_USD_M1.csv", file=sys.stderr)


if __name__ == "__main__":
    main()
