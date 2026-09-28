"""Indicadores técnicos vectorizados sobre paneles 'anchos' (índice = fecha, columnas = símbolos).

La misma función sirve para el backtest (miles de días) y para el escaneo diario (última fila),
de modo que lo que se evalúa en el histórico es exactamente lo que se usa en real.
Todos los cálculos usan solo datos hasta la fecha de cada fila (sin mirar al futuro).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

BTC = "BTCUSDT"


@dataclass
class Panel:
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    quote_volume: pd.DataFrame

    @classmethod
    def from_frames(cls, frames: dict[str, pd.DataFrame]) -> "Panel":
        fields = {}
        for field in ("open", "high", "low", "close", "quote_volume"):
            fields[field] = pd.DataFrame({sym: df[field] for sym, df in frames.items()}).sort_index()
        return cls(**fields)

    @property
    def symbols(self) -> list[str]:
        return list(self.close.columns)


def wilder(x: pd.DataFrame, period: int) -> pd.DataFrame:
    return x.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def atr(p: Panel, period: int = 14) -> pd.DataFrame:
    prev_close = p.close.shift(1)
    tr = np.maximum(p.high - p.low, np.maximum((p.high - prev_close).abs(), (p.low - prev_close).abs()))
    return wilder(pd.DataFrame(tr, index=p.close.index, columns=p.close.columns), period)


def rsi(close: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    delta = close.diff()
    gain = wilder(delta.clip(lower=0), period)
    loss = wilder(-delta.clip(upper=0), period)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def rolling_trend(logc: pd.DataFrame, n: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pendiente (log/día) y R² de una regresión lineal del log-precio sobre el tiempo, en ventana n.

    Se calcula con sumas móviles (O(T·N)) en lugar de una regresión por ventana.
    """
    t = pd.Series(np.arange(len(logc), dtype=float), index=logc.index)
    sum_y = logc.rolling(n).sum()
    sum_y2 = (logc**2).rolling(n).sum()
    sum_ty = logc.mul(t, axis=0).rolling(n).sum()
    start = t - (n - 1)                       # índice global del primer punto de la ventana
    sum_ky = sum_ty - sum_y.mul(start, axis=0)  # Σ k·y con k = 0..n-1 dentro de la ventana
    k_mean = (n - 1) / 2.0
    var_k = (n * n - 1) / 12.0
    y_mean = sum_y / n
    cov = sum_ky / n - k_mean * y_mean
    var_y = (sum_y2 / n - y_mean**2).clip(lower=0)
    slope = cov / var_k
    r2 = (cov**2) / (var_k * var_y.replace(0, np.nan))
    return slope, r2.clip(0, 1)


def compute(p: Panel, atr_period: int = 14, btc: str = BTC) -> dict[str, pd.DataFrame]:
    close = p.close
    logc = np.log(close)
    r1 = logc.diff()
    f: dict[str, pd.DataFrame] = {}
    f["close"] = close
    f["history_days"] = close.notna().cumsum().where(close.notna())
    for n in (7, 14, 28):
        f[f"ret_{n}"] = logc - logc.shift(n)
    f["vol_30"] = r1.rolling(30, min_periods=25).std()
    f["vol_30_ann"] = f["vol_30"] * np.sqrt(365)
    f["mom_14_adj"] = f["ret_14"] / (f["vol_30"] * np.sqrt(14))
    f["mom_28_adj"] = f["ret_28"] / (f["vol_30"] * np.sqrt(28))
    f["ema20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
    f["ema50"] = close.ewm(span=50, adjust=False, min_periods=50).mean()
    f["sma50"] = close.rolling(50).mean()
    f["sma200"] = close.rolling(200).mean()
    f["atr"] = atr(p, atr_period)
    f["atr_pct"] = f["atr"] / close
    f["rsi_14"] = rsi(close, 14)
    slope, r2 = rolling_trend(logc, 28)
    f["trend_slope_28"] = slope
    f["trend_r2_28"] = r2
    f["trend_quality_28"] = slope * r2
    qv = p.quote_volume
    f["qv_median_30"] = qv.rolling(30, min_periods=20).median()
    f["vol_z"] = np.log(qv.rolling(3).mean() / f["qv_median_30"].replace(0, np.nan))
    f["overext_atr"] = (close - f["ema20"]) / f["atr"]
    f["dist_high_28"] = close / p.high.rolling(28).max() - 1
    if btc in close.columns:
        f["rs_btc_14"] = f["ret_14"].sub(f["ret_14"][btc], axis=0)
    else:
        f["rs_btc_14"] = f["ret_14"] * np.nan
    f["returns"] = r1
    return f


def snapshot(features: dict[str, pd.DataFrame], date: pd.Timestamp | None = None) -> pd.DataFrame:
    """Corte transversal de todos los indicadores en una fecha (por defecto, la última)."""
    date = date if date is not None else features["close"].index[-1]
    return pd.DataFrame({name: frame.loc[date] for name, frame in features.items() if name != "returns"})


def correlation(returns: pd.DataFrame, a: str, b: str, window: int = 60) -> float:
    pair = returns[[a, b]].tail(window).dropna()
    if len(pair) < window // 2:
        return float("nan")
    return float(pair[a].corr(pair[b]))
