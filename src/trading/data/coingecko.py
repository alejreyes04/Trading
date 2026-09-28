"""CoinGecko: capitalización, FDV, datos globales y 'trending' (plan Demo gratuito: ~10.000 llamadas/mes).

Si existe la variable de entorno COINGECKO_API_KEY (clave Demo gratuita), se usa.
"""

from __future__ import annotations

import os

import pandas as pd

from trading.net import HttpClient

BASE = "https://api.coingecko.com/api/v3"


class CoinGecko:
    def __init__(self):
        key = os.environ.get("COINGECKO_API_KEY")
        headers = {"x-cg-demo-api-key": key} if key else {}
        # Sin clave el límite público es bajo: una llamada cada ~6 s es prudente.
        self.http = HttpClient(BASE, rate=1.0 if key else 0.15, headers=headers)

    def markets(self, pages: int = 3, per_page: int = 250) -> pd.DataFrame:
        rows: list[dict] = []
        for page in range(1, pages + 1):
            rows += self.http.get_json("/coins/markets", {
                "vs_currency": "usd", "order": "market_cap_desc", "per_page": per_page,
                "page": page, "price_change_percentage": "7d,30d",
            })
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df["symbol"] = df["symbol"].str.upper()
        keep = ["id", "symbol", "name", "current_price", "market_cap", "market_cap_rank",
                "fully_diluted_valuation", "total_volume", "ath_change_percentage",
                "price_change_percentage_7d_in_currency", "price_change_percentage_30d_in_currency"]
        return df[[c for c in keep if c in df.columns]]

    def global_data(self) -> dict:
        data = self.http.get_json("/global")["data"]
        return {
            "total_market_cap_usd": data["total_market_cap"].get("usd"),
            "market_cap_change_24h_pct": data.get("market_cap_change_percentage_24h_usd"),
            "btc_dominance_pct": data["market_cap_percentage"].get("btc"),
            "eth_dominance_pct": data["market_cap_percentage"].get("eth"),
        }

    def trending(self) -> list[dict]:
        data = self.http.get_json("/search/trending")
        return [
            {"symbol": c["item"]["symbol"].upper(), "name": c["item"]["name"],
             "market_cap_rank": c["item"].get("market_cap_rank")}
            for c in data.get("coins", [])
        ]
