"""Variantes de estrategia pre-registradas (ver research/hypotheses.md).

Cada variante cambia solo lo indicado; el resto (costes, motor de riesgo, elegibilidad) es idéntico a la v1.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from trading.config import Settings

MAJORS = ("BTCUSDT", "ETHUSDT")


@dataclass(frozen=True)
class Variant:
    name: str
    label: str
    select: str                      # "v1" | "pullback" | "majors_trend"
    regimes: tuple[str, ...]         # regímenes en los que se abren entradas nuevas
    use_regime_multiplier: bool      # si el tamaño se escala por régimen (v1: 1 / 0,5 / 0)
    stop_atr: float
    tp_atr: float | None             # None = sin objetivo
    max_days: int
    trend_exit: str | None = None    # "sma50": salida al cierre si cierre < SMA50
    random_baseline: bool = True
    notes: str = ""


VARIANTS: dict[str, Variant] = {
    "v1": Variant("v1", "v1 · a priori (momentum)", "v1", ("risk_on", "neutral"), True, 2.0, 3.0, 10),
    "v2a": Variant("v2a", "v2a · solo risk-on", "v1", ("risk_on",), True, 2.0, 3.0, 10,
                   notes="post-hoc (idea sacada del desglose por régimen de la v1)"),
    "v2b": Variant("v2b", "v2b · retroceso en tendencia", "pullback", ("risk_on", "neutral"), True, 2.0, 3.0, 10),
    "v2c": Variant("v2c", "v2c · tendencia BTC/ETH (semanas)", "majors_trend", ("risk_on", "neutral", "risk_off"),
                   False, 3.0, None, 90, trend_exit="sma50", random_baseline=False,
                   notes="fuera del horizonte de 2–10 días"),
}


def variant_settings(settings: Settings, v: Variant) -> Settings:
    """Ajustes con el stop/objetivo/duración de la variante (el motor de riesgo dimensiona con ellos)."""
    trade = settings.trade.model_copy(update={
        "stop_atr": v.stop_atr, "take_profit_atr": v.tp_atr if v.tp_atr is not None else 1e6,
        "max_holding_days": v.max_days})
    return settings.model_copy(update={"trade": trade})


def select_pullback(scored: pd.DataFrame, k: int) -> list[str]:
    """v2b: retroceso moderado dentro de una tendencia alcista, ordenado por momentum 28 d."""
    df = scored
    mask = ((df["close"] > df["ema50"]) & (df["ema20"] > df["ema50"])
            & (df["close"] <= df["ema20"] - 0.5 * df["atr"]) & (df["rsi_14"] >= 30))
    return df[mask.fillna(False)].sort_values("mom_28_adj", ascending=False).head(k).index.tolist()


def select_majors_trend(snap: pd.DataFrame) -> list[str]:
    """v2c: BTC/ETH con cierre > SMA50 > SMA200."""
    out = []
    for s in MAJORS:
        if s in snap.index:
            row = snap.loc[s]
            if pd.notna(row["sma200"]) and row["close"] > row["sma50"] > row["sma200"]:
                out.append(s)
    return out
