"""Métricas de evaluación: esperanza por operación con intervalo de confianza, drawdown, Sharpe y Brier."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy import stats


def bootstrap_mean_ci(x: np.ndarray, n_boot: int = 5000, alpha: float = 0.05, seed: int = 7) -> tuple[float, float]:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(n_boot, len(x)), replace=True).mean(axis=1)
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def trade_stats(r_multiples: list[float], pnls: list[float]) -> dict:
    r = np.array([v for v in r_multiples if v is not None and math.isfinite(v)], dtype=float)
    p = np.array([v for v in pnls if v is not None and math.isfinite(v)], dtype=float)
    if len(r) == 0:
        return {"n": 0}
    wins, losses = p[p > 0], p[p <= 0]
    lo, hi = bootstrap_mean_ci(r)
    return {
        "n": int(len(r)), "win_rate": float((p > 0).mean()), "avg_r": float(r.mean()),
        "avg_r_ci95": [lo, hi], "median_r": float(np.median(r)),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf"),
        "avg_win_eur": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_eur": float(losses.mean()) if len(losses) else 0.0,
        "total_pnl_eur": float(p.sum()),
    }


def equity_stats(equity: pd.Series, periods_per_year: float = 365) -> dict:
    equity = equity.dropna()
    if len(equity) < 3:
        return {}
    rets = equity.pct_change().dropna()
    dd = equity / equity.cummax() - 1
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    total = equity.iloc[-1] / equity.iloc[0] - 1
    sharpe = rets.mean() / rets.std() * math.sqrt(periods_per_year) if rets.std() > 0 else float("nan")
    downside = rets[rets < 0].std()
    sortino = rets.mean() / downside * math.sqrt(periods_per_year) if downside and downside > 0 else float("nan")
    return {
        "total_return": float(total),
        "cagr": float((1 + total) ** (1 / years) - 1) if total > -1 else -1.0,
        "vol_ann": float(rets.std() * math.sqrt(periods_per_year)),
        "sharpe": float(sharpe), "sortino": float(sortino), "max_drawdown": float(dd.min()),
    }


def probabilistic_sharpe(rets: pd.Series, sr_benchmark: float = 0.0) -> float:
    """PSR (Bailey & López de Prado): probabilidad de que el Sharpe real supere al de referencia,
    corrigiendo por asimetría y curtosis. Con una sola variante probada coincide con el Sharpe deflactado."""
    r = rets.dropna()
    n = len(r)
    if n < 10 or r.std() == 0:
        return float("nan")
    sr = r.mean() / r.std()
    skew, kurt = stats.skew(r), stats.kurtosis(r, fisher=False)
    denom = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr**2, 1e-12))
    return float(stats.norm.cdf((sr - sr_benchmark) * math.sqrt(n - 1) / denom))


def deflated_sharpe(rets: pd.Series, n_trials: int, sr_trials_std: float) -> float:
    """Sharpe deflactado: PSR frente al máximo Sharpe esperado por azar tras n_trials variantes."""
    if n_trials <= 1:
        return probabilistic_sharpe(rets, 0.0)
    emc = 0.5772156649
    z1 = stats.norm.ppf(1 - 1 / n_trials)
    z2 = stats.norm.ppf(1 - 1 / (n_trials * math.e))
    sr0 = sr_trials_std * ((1 - emc) * z1 + emc * z2)
    return probabilistic_sharpe(rets, sr0)


def brier(probs: list[float], outcomes: list[int]) -> float:
    pairs = [(p, o) for p, o in zip(probs, outcomes) if p is not None]
    if not pairs:
        return float("nan")
    return float(np.mean([(p - o) ** 2 for p, o in pairs]))


def weekly_report() -> str:
    """Informe semanal de las carteras (texto Markdown)."""
    from trading.config import load_settings
    from trading.data.bitvavo import Bitvavo
    from trading.ledger import PORTFOLIO_NAMES, PORTFOLIOS, Ledger, load_meta
    from trading.orders import fmt_eur
    from trading.paths import REPORTS_DIR
    from trading.pipeline import capital_for

    settings = load_settings()
    meta = load_meta()
    names = PORTFOLIO_NAMES
    lines = [f"# Informe semanal — {datetime.now(timezone.utc):%Y-%m-%d}", "",
             f"Desde {meta.get('start_date', '—')}. Todas las cifras son netas de comisiones y slippage estimado.", "",
             "| Cartera | Operaciones | Aciertos | R medio | IC 95 % del R medio | Profit factor | P&L | Caída máx. |",
             "|---|---|---|---|---|---|---|---|"]
    brier_rows = []
    for pf in PORTFOLIOS:
        led = Ledger(pf)
        closed = led.closed_trades()
        st = trade_stats([t.r_multiple for t in closed], [t.pnl_eur for t in closed])
        eq_stats = {}
        if led.equity_path.exists():
            eq = pd.read_csv(led.equity_path, parse_dates=["time"]).set_index("time")["equity"]
            eq_stats = equity_stats(eq)
        if st["n"] == 0:
            lines.append(f"| {names[pf]} | 0 | — | — | — | — | — | {eq_stats.get('max_drawdown', float('nan')):.1%} |")
            continue
        ci = st["avg_r_ci95"]
        lines.append(f"| {names[pf]} | {st['n']} | {st['win_rate']:.0%} | {st['avg_r']:+.2f} R | "
                     f"[{ci[0]:+.2f}, {ci[1]:+.2f}] | {st['profit_factor']:.2f} | {fmt_eur(st['total_pnl_eur'])} | "
                     f"{eq_stats.get('max_drawdown', float('nan')):.1%} |")
        if pf in ("QL", "live"):
            b = brier([t.llm_prob for t in closed], [1 if t.exit_reason == "objetivo" else 0 for t in closed])
            if math.isfinite(b):
                brier_rows.append(f"- Brier del agente en {names[pf]}: {b:.3f} (0 = perfecto; 0,25 = no mejor que decir 50 %)")
    try:
        start = pd.Timestamp(meta["start_date"], tz="UTC")
        btc = Bitvavo().candles("BTC-EUR", "1d", start_ms=int(start.timestamp() * 1000))
        if len(btc) > 1:
            lines += ["", f"Referencia BTC comprar y mantener desde el inicio: {btc['close'].iloc[-1] / btc['open'].iloc[0] - 1:+.1%}"]
    except Exception:
        pass
    lines += [""] + brier_rows
    lines += ["", "**Cómo leerlo:** un intervalo de confianza que incluye 0 significa que todavía no hay evidencia "
              "de ventaja. Con pocas operaciones es lo normal; no saques conclusiones antes de ~100 operaciones.",
              "Criterios para subir de etapa: ver README."]
    text = "\n".join(lines) + "\n"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / f"{datetime.now(timezone.utc):%Y-%m-%d}_semanal.md").write_text(text, encoding="utf-8")
    return text
