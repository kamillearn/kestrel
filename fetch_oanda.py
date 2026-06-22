import csv
import os
import sys
import time as _time
from datetime import datetime, timezone, timedelta
import requests

# ============== CONFIG — EDIT THESE ==============
# Token falls back to environment variable so it never lives in the source:
#   macOS/Linux:  export OANDA_API_TOKEN="your-practice-token"
#   Windows CMD:  set OANDA_API_TOKEN=your-practice-token
#   Windows PS:   $env:OANDA_API_TOKEN="your-practice-token"
OANDA_API_TOKEN = os.environ.get("OANDA_API_TOKEN", "bbb7bfbba94195c77a1a05b718ad56c1-b326e0775f227e8f1588eaa1b48f3af3")
ENVIRONMENT = "practice"
GRANULARITY = "M5"                # 1-minute candles for the Alpha Factory
START       = "2022-01-01T00:00:00Z"
END         = "2026-06-18T00:00:00Z"
PRICE       = "BA"                # "BA" = bid & ask candles → allows spread calculation

# EXPANDED: A global sweep of Index CFDs for the Alpha Factory to test
INSTRUMENTS = [
    # US Markets
    #("NAS100_USD", "data_oanda/NAS100_USD_M1.csv"),
    #("SPX500_USD", "data_oanda/SPX500_USD_M1.csv"),
    #("US2000_USD", "data_oanda/US2000_USD_M1.csv"),
    #("US30_USD",   "data_oanda/US30_USD_M1.csv"),   # Dow Jones
    
    # European Markets
    #("DE30_EUR",   "data_oanda/DE30_EUR_M1.csv"),   # DAX (Some OANDA accounts use DE40_EUR instead)
    #("UK100_GBP",  "data_oanda/UK100_GBP_M1.csv"),  # FTSE 100
    #("EU50_EUR",   "data_oanda/EU50_EUR_M1.csv"),   # Euro Stoxx 50
    
    # Asian/Pacific Markets
    #("JP225_USD",  "data_oanda/JP225_USD_M1.csv"),  # Nikkei 225
    #("HK33_HKD",   "data_oanda/HK33_HKD_M1.csv"),   # Hang Seng
    ("BTC_USD",  "data_oanda/BTC_USD_M5.csv")   # ASX 200
]
# =================================================

HOST = "https://api-fxpractice.oanda.com" if ENVIRONMENT == "practice" else "https://api-fxtrade.oanda.com"
HEADERS = {"Authorization": f"Bearer {OANDA_API_TOKEN}"}
MAX_COUNT = 5000


def to_dt(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def granularity_to_minutes(gran: str) -> int:
    if gran.startswith('M'):
        return int(gran[1:])
    elif gran.startswith('H'):
        return int(gran[1:]) * 60
    elif gran.startswith('D'):
        return 1440
    elif gran.startswith('W'):
        return 10080
    else:
        return int(gran)


def list_instruments():
    """Print the authoritative, account-specific tradeable-instrument list."""
    acc = requests.get(f"{HOST}/v3/accounts", headers=HEADERS, timeout=30)
    acc.raise_for_status()
    account_id = acc.json()["accounts"][0]["id"]
    r = requests.get(f"{HOST}/v3/accounts/{account_id}/instruments", headers=HEADERS, timeout=30)
    r.raise_for_status()
    insts = sorted(r.json()["instruments"], key=lambda x: (x["type"], x["name"]))
    print(f"Account {account_id} — {len(insts)} instruments\n")
    print(f"{'name':16}{'type':12}displayName")
    print("-" * 50)
    for i in insts:
        print(f"{i['name']:16}{i['type']:12}{i.get('displayName', '')}")


def fetch(instrument, output_file):
    """Fetch candles for a single instrument and write to CSV."""
    print(f"\n=== Fetching {instrument} -> {output_file} ===")
    
    # Ensure the target directory exists
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    
    rows = []
    cursor = to_dt(START)
    end_dt = to_dt(END)
    request_no = 0
    step_minutes = granularity_to_minutes(GRANULARITY)
    url = f"{HOST}/v3/instruments/{instrument}/candles"

    while cursor < end_dt:
        params = {
            "granularity": GRANULARITY,
            "price": PRICE,
            "from": cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count": MAX_COUNT,
        }
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=30)
        except Exception as e:
            print(f"  Request error: {e}")
            break

        if r.status_code != 200:
            print(f"  ERROR {r.status_code}: {r.text[:200]}")
            break

        candles = r.json().get("candles", [])
        if not candles:
            print("  No more candles returned by API.")
            break

        new_last = None
        for c in candles:
            t = to_dt(c["time"][:19] + "Z")
            if t >= end_dt:
                break
            if not c["complete"]:
                continue

            bid = c.get("bid")
            ask = c.get("ask")
            if not bid or not ask:
                continue

            # Midpoint OHLC
            mid_open   = (float(bid["o"]) + float(ask["o"])) / 2.0
            mid_high   = (float(bid["h"]) + float(ask["h"])) / 2.0
            mid_low    = (float(bid["l"]) + float(ask["l"])) / 2.0
            mid_close  = (float(bid["c"]) + float(ask["c"])) / 2.0
            spread_points = float(ask["c"]) - float(bid["c"])

            rows.append([
                c["time"][:19].replace("T", " "),
                round(mid_open, 5), round(mid_high, 5),
                round(mid_low, 5), round(mid_close, 5),
                int(c["volume"]),
                round(spread_points, 5)
            ])
            new_last = t

        request_no += 1
        print(f"  request {request_no}: {len(candles)} candles processed, total kept {len(rows)}")

        if new_last is None:
            # Prevent infinite loop if API returns data but it doesn't advance past our cursor
            break

        # Advance cursor to the next expected candle slot after the last received block
        cursor = new_last + timedelta(minutes=step_minutes)
        _time.sleep(0.15)

    # Write CSV after loop finishes execution to prevent performance overhead
    if rows:
        with open(output_file, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time", "open", "high", "low", "close", "volume", "spread"])
            w.writerows(rows)
            print(f"  Wrote {len(rows)} candles to {output_file}")
    else:
        print(f"  No data fetched for {instrument}")


def main():
    if OANDA_API_TOKEN in [".....-.....", "PASTE_YOUR_NEW_TOKEN_HERE", ""]:
        raise SystemExit("ERROR: Please set OANDA_API_TOKEN in your script config "
                         "or export it as an environment variable first.")

    # `python fetch_oanda.py --list` -> show what your account can actually pull
    if "--list" in sys.argv:
        list_instruments()
        return

    # Guard against two instruments writing to the same file (a silent overwrite)
    outfiles = [o for _, o in INSTRUMENTS]
    dupes = {o for o in outfiles if outfiles.count(o) > 1}
    if dupes:
        raise SystemExit(f"ERROR: duplicate output filenames {dupes} — "
                         "each instrument needs its own CSV.")

    print(f"Fetching {GRANULARITY} from {START} to {END} for {len(INSTRUMENTS)} instrument(s)...")
    for instrument, outfile in INSTRUMENTS:
        fetch(instrument, outfile)
    
    print("\nAll done.")


if __name__ == "__main__":
    main()