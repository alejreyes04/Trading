"""Régimen de mercado y puntuación transversal de candidatas.

Los pesos se fijan a priori en config (no se optimizan) para no sobreajustar al histórico.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trading.config import Settings


@dataclass
class RegimeState:
    label: str          # risk_on | neutral | risk_off
    multiplier: float
    btc_close: float
    btc_sma50: float
    btc_sma200: float
    btc_above_sma200: bool
    breadth: float      # % del universo elegible con cierre > EMA50
    n_eligible: int

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def regime(snap: pd.DataFrame, eligible: pd.Series, settings: Settings, btc: str = "BTCUSDT") -> RegimeState:
    btc_row = snap.loc[btc]
    above = bool(btc_row["close"] > btc_row["sma200"]) if pd.notna(btc_row["sma200"]) else False
    elig = snap[eligible.reindex(snap.index, fill_value=False)]
    breadth = float((elig["close"] > elig["ema50"]).mean()) if len(elig) else 0.0
    wide = breadth >= settings.regime.breadth_threshold
    label = "risk_on" if (above and wide) else ("neutral" if (above or wide) else "risk_off")
    return RegimeState(label, settings.regime.multipliers[label], float(btc_row["close"]),
                       float(btc_row["sma50"]), float(btc_row["sma200"]), above, breadth, int(len(elig)))


def _pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True)


def score(snap: pd.DataFrame, eligible: pd.Series, settings: Settings) -> pd.DataFrame:
    """Devuelve el universo elegible con bloques, puntuación compuesta y marca de candidata."""
    df = snap[eligible.reindex(snap.index, fill_value=False)].copy()
    if df.empty:
        return df.assign(score=pd.Series(dtype=float), candidate=pd.Series(dtype=bool))
    w = settings.signals.weights
    df["b_trend"] = pd.concat([_pct_rank(df["mom_28_adj"]), _pct_rank(df["mom_14_adj"]),
                               _pct_rank(df["trend_quality_28"])], axis=1).mean(axis=1)
    df["b_relative"] = pd.concat([_pct_rank(df["rs_btc_14"]), _pct_rank(df["dist_high_28"])], axis=1).mean(axis=1)
    df["b_participation"] = _pct_rank(df["vol_z"])
    df["b_entry_quality"] = _pct_rank(-df["overext_atr"])
    df["score"] = (w.trend * df["b_trend"] + w.relative * df["b_relative"]
                   + w.participation * df["b_participation"] + w.entry_quality * df["b_entry_quality"])
    df["score"] /= (w.trend + w.relative + w.participation + w.entry_quality)
    sig = settings.signals
    df["candidate"] = (
        (df["close"] > df["ema20"]) & (df["ema20"] > df["ema50"]) & (df["mom_28_adj"] > 0)
        & (df["overext_atr"] <= sig.max_overextension_atr) & (df["rsi_14"] <= sig.max_rsi)
        & df[["score", "atr_pct"]].notna().all(axis=1)
    )
    return df.sort_values("score", ascending=False)


def eligibility(snap: pd.DataFrame, settings: Settings, excluded: set[str] | None = None) -> pd.Series:
    """Filtros de universo que dependen del histórico (antigüedad y volumen en Binance)."""
    u = settings.universe
    ok = (snap["history_days"] >= u.min_history_days) & (snap["qv_median_30"] >= u.min_binance_quote_volume_usd)
    ok &= snap[["close", "ema50", "atr"]].notna().all(axis=1)
    if excluded:
        ok &= ~snap.index.isin(list(excluded))
    return ok.fillna(False)


def slippage_for(qv_median_usd: float, settings: Settings) -> float:
    c = settings.costs
    if not np.isfinite(qv_median_usd):
        return c.slippage_low_liquidity
    if qv_median_usd >= 50e6:
        return c.slippage_high_liquidity
    if qv_median_usd >= 10e6:
        return c.slippage_mid_liquidity
    return c.slippage_low_liquidity
