"""CSV loader -> ET-indexed bars."""
from __future__ import annotations
import pandas as pd
from kestrel.utils.sessions import to_eastern
def load_csv(path, source_tz="UTC"):
    df = pd.read_csv(path)
    if "spread" not in df.columns: df["spread"] = 0.0
    return to_eastern(df, "time", source_tz)
