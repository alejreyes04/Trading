"""Construye el universo operable: monedas con datos en Binance (señal) Y mercado EUR en Bitvavo (ejecución).

Cada moneda descartada queda registrada con su motivo, para que el informe muestre el 'embudo'.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trading.config import Exclusions, Settings

# Tickers de Binance que cotizan multiplicados (1000PEPE = 1000 PEPE, 1MBABYDOGE = 1e6 BABYDOGE).
MULTIPLIER_PREFIXES = (("1000000", 1_000_000), ("1M", 1_000_000), ("1000", 1_000))


def split_multiplier(base: str) -> tuple[str, int]:
    for prefix, factor in MULTIPLIER_PREFIXES:
        if base.startswith(prefix) and len(base) > len(prefix) and not base[len(prefix)].isdigit():
            return base[len(prefix):], factor
    return base, 1


def build(binance_symbols: pd.DataFrame, binance_tickers: pd.DataFrame, bitvavo_markets: pd.DataFrame,
          bitvavo_tickers: pd.DataFrame, coingecko: pd.DataFrame | None, settings: Settings,
          exclusions: Exclusions) -> pd.DataFrame:
    u = settings.universe
    last_usdt = binance_tickers.set_index("symbol")["lastPrice"]
    eur_usdt = float(last_usdt.get("EURUSDT", np.nan))  # USDT por 1 EUR
    bv_by_base = {row.base: market for market, row in bitvavo_markets.iterrows()}

    rows = []
    for sym, base in zip(binance_symbols["symbol"], binance_symbols["base"]):
        asset, factor = split_multiplier(base)
        row = {"binance_symbol": sym, "base": asset, "binance_base": base, "factor": factor,
               "bitvavo_market": None, "reason": None}
        if exclusions.is_excluded(asset) or exclusions.is_excluded(base):
            row["reason"] = "excluida (stable/wrapped/apalancada/manual)"
            rows.append(row)
            continue
        market = bv_by_base.get(asset)
        if market is None:
            row["reason"] = "no cotiza en Bitvavo"
            rows.append(row)
            continue
        row["bitvavo_market"] = market
        t = bitvavo_tickers.loc[market] if market in bitvavo_tickers.index else None
        if t is None or not np.isfinite(t.get("ask", np.nan)) or not np.isfinite(t.get("bid", np.nan)):
            row["reason"] = "sin precio en Bitvavo"
            rows.append(row)
            continue
        mid = (t["bid"] + t["ask"]) / 2
        row.update({
            "price_eur": float(t["last"]), "bid_eur": float(t["bid"]), "ask_eur": float(t["ask"]),
            "spread_bps": float((t["ask"] - t["bid"]) / mid * 1e4),
            "bitvavo_vol_eur": float(t["volumeQuote"]) if np.isfinite(t["volumeQuote"]) else 0.0,
        })
        binance_eur = last_usdt.get(sym, np.nan) / factor / eur_usdt
        row["price_mismatch"] = float(abs(binance_eur / mid - 1)) if np.isfinite(binance_eur) else np.nan
        if not np.isfinite(row["price_mismatch"]) or row["price_mismatch"] > u.max_price_mismatch:
            row["reason"] = "precio Binance≠Bitvavo (posible ticker distinto)"
        elif row["bitvavo_vol_eur"] < u.min_bitvavo_volume_eur:
            row["reason"] = "poco volumen en Bitvavo"
        elif row["spread_bps"] > u.max_bitvavo_spread_bps:
            row["reason"] = "spread alto en Bitvavo"
        rows.append(row)

    df = pd.DataFrame(rows).set_index("binance_symbol")
    if coingecko is not None and not coingecko.empty:
        df = _attach_coingecko(df, coingecko, eur_usdt)
        too_diluted = df["fdv_mc"] > u.max_fdv_mc_ratio
        df.loc[df["reason"].isna() & too_diluted, "reason"] = "dilución alta (FDV/MC)"
    df["eligible_static"] = df["reason"].isna()
    return df


def _attach_coingecko(df: pd.DataFrame, cg: pd.DataFrame, eur_usdt: float) -> pd.DataFrame:
    """Empareja por símbolo; si hay varios con el mismo símbolo, el de precio coherente y mayor capitalización."""
    cg = cg.sort_values("market_cap", ascending=False)
    out = {"cg_id": [], "market_cap": [], "fdv": [], "mc_rank": []}
    for _, row in df.iterrows():
        match = None
        if row.get("price_eur") is not None and np.isfinite(row.get("price_eur", np.nan)):
            price_usd = row["price_eur"] * eur_usdt
            for _, c in cg[cg["symbol"] == row["base"]].iterrows():
                if c["current_price"] and abs(c["current_price"] / price_usd - 1) < 0.05:
                    match = c
                    break
        out["cg_id"].append(match["id"] if match is not None else None)
        out["market_cap"].append(match["market_cap"] if match is not None else np.nan)
        out["fdv"].append(match["fully_diluted_valuation"] if match is not None else np.nan)
        out["mc_rank"].append(match["market_cap_rank"] if match is not None else np.nan)
    for k, v in out.items():
        df[k] = v
    df["fdv_mc"] = pd.to_numeric(df["fdv"], errors="coerce") / pd.to_numeric(df["market_cap"], errors="coerce")
    return df


def funnel(df: pd.DataFrame) -> dict[str, int]:
    counts = df["reason"].fillna("pasa filtros de universo").value_counts()
    return {k: int(v) for k, v in counts.items()}
