"""Datos de mercado spot de Binance vía data-api.binance.vision (endpoint público de solo datos).

api.binance.com devuelve 451 desde la nube (bloqueo geográfico); data-api.binance.vision no.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from trading.net import HttpClient

BASE = "https://data-api.binance.vision"
KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
              "quote_volume", "trades", "taker_base", "taker_quote", "ignore"]


class BinanceVision:
    def __init__(self, rate: float = 10.0):
        self.http = HttpClient(BASE, rate=rate)

    def spot_symbols(self, quote: str = "USDT") -> pd.DataFrame:
        info = self.http.get_json("/api/v3/exchangeInfo", {"permissions": "SPOT"})
        rows = [
            {"symbol": s["symbol"], "base": s["baseAsset"], "quote": s["quoteAsset"]}
            for s in info["symbols"]
            if s["status"] == "TRADING" and s["quoteAsset"] == quote
        ]
        return pd.DataFrame(rows)

    def tickers_24h(self) -> pd.DataFrame:
        data = self.http.get_json("/api/v3/ticker/24hr")
        df = pd.DataFrame(data)
        for col in ("lastPrice", "quoteVolume", "volume", "priceChangePercent"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def klines(self, symbol: str, interval: str = "1d", limit: int = 220,
               start_ms: int | None = None, drop_open: bool = True) -> pd.DataFrame:
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_ms is not None:
            params["startTime"] = start_ms
        raw = self.http.get_json("/api/v3/klines", params)
        df = klines_to_frame(raw)
        if drop_open and not df.empty:
            now_ms = int(time.time() * 1000)
            df = df[df["close_time_ms"] < now_ms]
        return df.drop(columns=["close_time_ms"])

    def klines_many(self, symbols: list[str], interval: str = "1d", limit: int = 220,
                    workers: int = 8) -> dict[str, pd.DataFrame]:
        def fetch(sym: str) -> tuple[str, pd.DataFrame | None]:
            try:
                return sym, self.klines(sym, interval, limit)
            except Exception:  # un símbolo que falla no debe tumbar el escaneo
                return sym, None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = dict(pool.map(fetch, symbols))
        return {k: v for k, v in results.items() if v is not None and not v.empty}


def klines_to_frame(raw: list[list]) -> pd.DataFrame:
    """Convierte klines crudas (API o CSV del archivo histórico) en DataFrame indexado por fecha UTC."""
    if not raw:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "quote_volume", "close_time_ms"])
    df = pd.DataFrame(raw, columns=KLINE_COLS[: len(raw[0])])
    open_time = pd.to_numeric(df["open_time"])
    # El archivo histórico spot usa microsegundos desde 2025; la API, milisegundos.
    open_time = open_time.where(open_time < 10**14, open_time // 1000)
    close_time = pd.to_numeric(df["close_time"])
    close_time = close_time.where(close_time < 10**14, close_time // 1000)
    out = pd.DataFrame({
        "open": pd.to_numeric(df["open"]),
        "high": pd.to_numeric(df["high"]),
        "low": pd.to_numeric(df["low"]),
        "close": pd.to_numeric(df["close"]),
        "volume": pd.to_numeric(df["volume"]),
        "quote_volume": pd.to_numeric(df["quote_volume"]),
        "close_time_ms": close_time.astype("int64"),
    })
    out.index = pd.to_datetime(open_time.astype("int64"), unit="ms", utc=True)
    out.index.name = "time"
    return out[~out.index.duplicated(keep="last")].sort_index()
