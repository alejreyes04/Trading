"""Bitvavo (exchange de ejecución): mercados en EUR, precios, libro y velas. Solo endpoints públicos."""

from __future__ import annotations

import pandas as pd

from trading.net import HttpClient

BASE = "https://api.bitvavo.com/v2"


class Bitvavo:
    def __init__(self, rate: float = 5.0):
        self.http = HttpClient(BASE, rate=rate)

    def markets(self, quote: str = "EUR") -> pd.DataFrame:
        df = pd.DataFrame(self.http.get_json("/markets"))
        df = df[(df["status"] == "trading") & (df["quote"] == quote)].copy()
        for col in ("minOrderInBaseAsset", "minOrderInQuoteAsset", "tickSize"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["quantityDecimals"] = pd.to_numeric(df["quantityDecimals"], errors="coerce").fillna(8).astype(int)
        return df[["market", "base", "quote", "tickSize", "quantityDecimals",
                   "minOrderInBaseAsset", "minOrderInQuoteAsset", "orderTypes"]].set_index("market")

    def tickers(self) -> pd.DataFrame:
        """Último precio, volumen 24 h y mejor bid/ask de todos los mercados (2 llamadas)."""
        t24 = pd.DataFrame(self.http.get_json("/ticker/24h"))
        book = pd.DataFrame(self.http.get_json("/ticker/book"))
        t24 = t24[["market", "last", "volumeQuote", "open", "high", "low"]]
        book = book[["market", "bid", "ask"]]
        df = t24.merge(book, on="market", how="outer").set_index("market")
        return df.apply(pd.to_numeric, errors="coerce")

    def candles(self, market: str, interval: str = "1h", start_ms: int | None = None,
                end_ms: int | None = None, limit: int = 1000) -> pd.DataFrame:
        params: dict = {"interval": interval, "limit": limit}
        if start_ms is not None:
            params["start"] = start_ms
        if end_ms is not None:
            params["end"] = end_ms
        raw = self.http.get_json(f"/{market}/candles", params)
        if not raw:
            return empty_candles()
        df = pd.DataFrame(raw, columns=["time", "open", "high", "low", "close", "volume"])
        df.index = pd.to_datetime(df.pop("time").astype("int64"), unit="ms", utc=True)
        return df.apply(pd.to_numeric).sort_index()


def empty_candles() -> pd.DataFrame:
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                        index=pd.DatetimeIndex([], tz="UTC"), dtype=float)
