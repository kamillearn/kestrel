"""Append-only trade/day journal (CSV)."""
from __future__ import annotations
import csv
from pathlib import Path
from datetime import date

class Journal:
    def __init__(self, path="journal.csv"):
        self.path = Path(path)
        if not self.path.exists():
            self.path.write_text("date,instrument,side,equity\n")
    def record_day(self, d: date, instrument: str, side, equity: float):
        with self.path.open("a", newline="") as f:
            csv.writer(f).writerow([d.isoformat(), instrument, side or "", round(equity, 2)])
