import math
from datetime import datetime, timedelta, timezone

import pandas as pd

from trading.ledger import Ledger
from trading.orders import MarketRules
from trading.pipeline import run_v2c
from trading.risk import trade_levels
from trading.strategies import VARIANTS, variant_settings

NOW = datetime(2026, 9, 29, 6, 0, tzinfo=timezone.utc)
MARKETS = pd.DataFrame({"tickSize": [1.0, 0.01], "quantityDecimals": [8, 8], "minOrderInQuoteAsset": [5.0, 5.0],
                        "minOrderInBaseAsset": [0.0, 0.0]}, index=["BTC-EUR", "ETH-EUR"])
ASKS = {"BTC-EUR": 60000.0, "ETH-EUR": 2000.0}
BIDS = {"BTC-EUR": 59990.0, "ETH-EUR": 1999.0}


def snap(btc_close: float, eth_close: float) -> pd.DataFrame:
    return pd.DataFrame({"close": [btc_close, eth_close], "sma50": [100.0, 100.0], "sma200": [90.0, 90.0],
                         "atr_pct": [0.03, 0.04], "qv_median_30": [1e9, 5e8]}, index=["BTCUSDT", "ETHUSDT"])


def test_trade_levels_without_target(settings):
    s = variant_settings(settings, VARIANTS["v2c"])
    stop, _, tp = trade_levels(100.0, 0.03, s, MarketRules(0.01, 8, 5.0, 0.0))
    assert math.isinf(tp) and abs(stop - 91.0) < 0.011          # stop a 3 ATR, sin objetivo


def test_v2c_enters_trending_major_only(tmp_path, settings):
    led = Ledger("V2C", root=tmp_path)
    out = run_v2c(led, snap(110.0, 95.0), MARKETS, ASKS, BIDS, None, settings, "r1", NOW)
    assert [t.base for t in out["entries"]] == ["BTC"] and out["in_trend"] == ["BTCUSDT"]
    t = out["entries"][0]
    assert t.market == "BTC-EUR" and t.status == "open" and math.isinf(t.tp_price)
    assert abs(t.stop_price / t.entry_limit - (1 - 3 * 0.03)) < 1e-3
    assert datetime.fromisoformat(t.time_exit_at) - NOW == timedelta(days=90)
    assert abs(t.risk_eur - 5.0) < 0.5                          # 1 % de 500 €, sin multiplicador de régimen
    led.save()
    again = Ledger("V2C", root=tmp_path)
    assert math.isinf(again.trades[0].tp_price) and again.trades[0].status == "open"
    # Con la posición abierta no se repite la entrada.
    out2 = run_v2c(again, snap(112.0, 95.0), MARKETS, ASKS, BIDS, None, settings, "r2", NOW + timedelta(hours=6))
    assert out2["entries"] == [] and ("BTC", "ya hay posición abierta") in out2["plan"].rejected


def test_v2c_trend_exit_when_close_below_sma50(tmp_path, settings):
    led = Ledger("V2C", root=tmp_path)
    run_v2c(led, snap(110.0, 95.0), MARKETS, ASKS, BIDS, None, settings, "r1", NOW)
    later = NOW + timedelta(days=1)
    out = run_v2c(led, snap(98.0, 95.0), MARKETS, ASKS, {"BTC-EUR": 58000.0, "ETH-EUR": 1990.0}, None,
                  settings, "r2", later)
    assert [t.exit_reason for t in out["exits"]] == ["tendencia"] and out["entries"] == []
    t = out["exits"][0]
    assert t.status == "closed" and t.exit_price < 58000.0 and t.pnl_eur < 0
