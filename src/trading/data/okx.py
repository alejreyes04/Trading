"""OKX (datos públicos de perpetuos): funding y open interest como medida de posicionamiento.

La API de futuros de Binance está bloqueada desde la nube (451); la de OKX no.
"""

from __future__ import annotations

import pandas as pd

from trading.net import HttpClient

BASE = "https://www.okx.com"


class OKX:
    def __init__(self, rate: float = 5.0):
        self.http = HttpClient(BASE, rate=rate)

    def usdt_swaps(self) -> set[str]:
        data = self.http.get_json("/api/v5/public/instruments", {"instType": "SWAP"})["data"]
        return {d["instId"] for d in data if d["instId"].endswith("-USDT-SWAP") and d.get("state") == "live"}

    def funding_rate(self, base: str) -> float | None:
        data = self.http.get_json("/api/v5/public/funding-rate", {"instId": f"{base}-USDT-SWAP"})["data"]
        return float(data[0]["fundingRate"]) if data else None

    def open_interest(self) -> pd.DataFrame:
        data = self.http.get_json("/api/v5/public/open-interest", {"instType": "SWAP"})["data"]
        df = pd.DataFrame(data)
        if df.empty:
            return df
        df = df[df["instId"].str.endswith("-USDT-SWAP")].copy()
        df["base"] = df["instId"].str.replace("-USDT-SWAP", "", regex=False)
        df["oi_usd"] = pd.to_numeric(df.get("oiUsd"), errors="coerce")
        return df[["base", "oi_usd"]].set_index("base")
