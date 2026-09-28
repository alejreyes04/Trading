import numpy as np
import pandas as pd
import pytest

from trading.config import load_settings


@pytest.fixture
def settings():
    return load_settings()


def make_ohlc(closes, start="2024-01-01", freq="D", spread=0.01):
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    c = pd.Series(closes, index=idx, dtype=float)
    o = c.shift(1).fillna(c.iloc[0])
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * (1 + spread), "low": np.minimum(o, c) * (1 - spread),
                         "close": c, "quote_volume": 1e7, "volume": 1.0})
