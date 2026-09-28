import numpy as np
import pandas as pd

from trading import features
from tests.conftest import make_ohlc


def test_rolling_trend_matches_polyfit():
    rng = np.random.default_rng(1)
    y = np.cumsum(rng.normal(0.001, 0.02, 120))
    logc = pd.DataFrame({"A": y})
    slope, r2 = features.rolling_trend(logc, 28)
    window = y[-28:]
    k = np.arange(28)
    b, a = np.polyfit(k, window, 1)
    pred = a + b * k
    r2_ref = 1 - ((window - pred) ** 2).sum() / ((window - window.mean()) ** 2).sum()
    assert abs(slope["A"].iloc[-1] - b) < 1e-9
    assert abs(r2["A"].iloc[-1] - r2_ref) < 1e-9


def test_atr_and_rsi_basic():
    df = make_ohlc(np.linspace(100, 130, 60))
    p = features.Panel.from_frames({"A": df, "BTCUSDT": df})
    f = features.compute(p)
    tr = (df["high"] - df["low"]).combine((df["high"] - df["close"].shift()).abs(), max)
    assert f["atr"]["A"].iloc[-1] > 0
    assert f["rsi_14"]["A"].iloc[-1] > 70          # subida constante → RSI alto
    assert f["history_days"]["A"].iloc[-1] == 60
    assert abs(f["rs_btc_14"]["A"].iloc[-1]) < 1e-12   # igual que BTC → fuerza relativa 0
    assert not tr.empty


def test_no_lookahead_last_row_uses_only_past():
    df = make_ohlc(np.linspace(100, 130, 80))
    p1 = features.Panel.from_frames({"A": df.iloc[:70], "BTCUSDT": df.iloc[:70]})
    p2 = features.Panel.from_frames({"A": df, "BTCUSDT": df})
    f1, f2 = features.compute(p1), features.compute(p2)
    d = df.index[69]
    for name in ("ema20", "atr", "rsi_14", "mom_28_adj", "trend_quality_28", "vol_z"):
        assert np.isclose(f1[name]["A"].loc[d], f2[name]["A"].loc[d], equal_nan=True), name
