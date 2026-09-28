import numpy as np
import pandas as pd

from trading.strategies import VARIANTS, select_majors_trend, select_pullback, variant_settings


def test_pullback_selection():
    df = pd.DataFrame({
        "close": [95.0, 101.0, 95.0, 80.0],
        "ema20": [100.0, 100.0, 100.0, 100.0],
        "ema50": [90.0, 90.0, 90.0, 90.0],
        "atr": [5.0, 5.0, 5.0, 5.0],
        "rsi_14": [40.0, 55.0, 25.0, 40.0],
        "mom_28_adj": [1.0, 2.0, 3.0, 4.0],
    }, index=["A", "B", "C", "D"])
    # A: retroceso válido. B: por encima de EMA20. C: RSI < 30. D: bajo la EMA50.
    assert select_pullback(df, 12) == ["A"]


def test_majors_trend_selection():
    snap = pd.DataFrame({"close": [110.0, 90.0], "sma50": [100.0, 100.0], "sma200": [95.0, 95.0]},
                        index=["BTCUSDT", "ETHUSDT"])
    assert select_majors_trend(snap) == ["BTCUSDT"]


def test_variant_settings_override(settings):
    s2 = variant_settings(settings, VARIANTS["v2c"])
    assert s2.trade.stop_atr == 3.0 and s2.trade.max_holding_days == 90 and s2.trade.take_profit_atr is None
    assert settings.trade.stop_atr == 2.0          # los ajustes originales no cambian


def test_v2c_trend_exit_end_to_end(settings):
    from trading import features
    from trading.backtest import precompute, simulate

    n = 330
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    up = np.linspace(100, 200, 280)
    down = np.linspace(200, 150, n - 280)
    close = pd.Series(np.concatenate([up, down]), index=idx)
    df = pd.DataFrame({"open": close.shift(1).fillna(close.iloc[0]), "close": close,
                       "high": close * 1.01, "low": close * 0.99, "quote_volume": 1e9, "volume": 1.0})
    panel = features.Panel.from_frames({"BTCUSDT": df})
    pre = precompute(panel, settings, idx[220], idx[-1])
    res = simulate(pre, panel, settings, "Q", variant=VARIANTS["v2c"])
    reasons = set(res["trades"]["reason"])
    assert "tendencia" in reasons or "stop" in reasons
    assert len(res["trades"]) >= 1
