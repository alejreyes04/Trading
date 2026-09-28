"""Evaluación de las variantes pre-registradas (research/hypotheses.md) con sus criterios fijados de antemano."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from trading.backtest import benchmarks, load_panel, precompute, run_random, simulate
from trading.config import load_settings
from trading.evaluate import bootstrap_mean_ci, deflated_sharpe, equity_stats
from trading.paths import REPORTS_DIR
from trading.strategies import VARIANTS

SPLIT = pd.Timestamp("2024-01-01", tz="UTC")
N_TRIALS = 4   # v1 + v2a + v2b + v2c


def _half_returns(eq: pd.Series) -> tuple[float, float]:
    first, second = eq[eq.index < SPLIT], eq[eq.index >= SPLIT]
    r1 = first.iloc[-1] / first.iloc[0] - 1 if len(first) > 1 else float("nan")
    r2 = second.iloc[-1] / first.iloc[-1] - 1 if len(second) and len(first) else float("nan")
    return float(r1), float(r2)


def run_research(start: str = "2021-01-01", end: str | None = None, random_runs: int = 50) -> str:
    settings = load_settings()
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") if end else pd.Timestamp.now(tz="UTC")
    panel = load_panel((start_ts - pd.Timedelta(days=260)).strftime("%Y-%m-%d"), refresh=False)
    pre = precompute(panel, settings, start_ts, end_ts)
    bench = benchmarks(pre, panel, settings)
    btc_stats = equity_stats(bench["BTC comprar y mantener"])

    results = {}
    for name, v in VARIANTS.items():
        res = simulate(pre, panel, settings, "Q", variant=v)
        eq = res["equity"]
        trades = res["trades"]
        r = trades["r"].to_numpy() if not trades.empty else np.array([])
        rets = eq.pct_change().dropna()
        entry = {
            "label": v.label, "notes": v.notes, "stats": equity_stats(eq),
            "n_trades": int(len(trades)), "avg_r": float(np.mean(r)) if len(r) else float("nan"),
            "avg_r_ci95": list(bootstrap_mean_ci(r)) if len(r) > 1 else [float("nan")] * 2,
            "win_rate": float((trades["pnl"] > 0).mean()) if len(trades) else float("nan"),
            "exits": trades["reason"].value_counts().to_dict() if len(trades) else {},
            "halves": _half_returns(eq), "sr_daily": float(rets.mean() / rets.std()) if rets.std() > 0 else 0.0,
            "rets": rets, "exposure": res["exposure"], "fees": res["fees"],
        }
        if v.random_baseline and name != "v1":
            rnd = run_random(pre, panel, settings, random_runs, variant_name=name)
            rs = np.array([equity_stats(x["equity"]).get("sharpe", np.nan) for x in rnd])
            rt = np.array([equity_stats(x["equity"]).get("total_return", np.nan) for x in rnd])
            entry["random"] = {"runs": random_runs, "sharpe_median": float(np.nanmedian(rs)),
                               "total_return_median": float(np.nanmedian(rt)),
                               "pct_rank_sharpe": float((rs < entry["stats"].get("sharpe", np.nan)).mean())}
        results[name] = entry

    sr_std = float(np.std([e["sr_daily"] for e in results.values()], ddof=1))
    for name, e in results.items():
        e["dsr"] = deflated_sharpe(e.pop("rets"), N_TRIALS, sr_std)
        if name == "v1":
            continue
        crit = {
            "R medio > 0 y Sharpe deflactado ≥ 0,90": bool(e["avg_r"] > 0 and e["dsr"] >= 0.90),
            "rentabilidad > 0 en 2021–2023 y en 2024–2026": bool(all(x > 0 for x in e["halves"])),
            "caída máxima ≤ 25 %": bool(e["stats"].get("max_drawdown", -1) >= -0.25),
        }
        if "random" in e:
            crit["percentil ≥ 80 en Sharpe frente al azar"] = bool(e["random"]["pct_rank_sharpe"] >= 0.80)
        else:
            crit["Sharpe > Sharpe de BTC comprar y mantener"] = bool(
                e["stats"].get("sharpe", -9) > btc_stats.get("sharpe", 9))
        e["criteria"] = crit
        e["passes"] = all(crit.values())
    return write_research_report(results, btc_stats, start, end_ts, sr_std)


def write_research_report(results: dict, btc_stats: dict, start: str, end_ts: pd.Timestamp, sr_std: float) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def pc(x):
        return "—" if x is None or not math.isfinite(x) else f"{x * 100:+.1f} %".replace(".", ",")

    def nm(x, f="{:.2f}"):
        return "—" if x is None or not math.isfinite(x) else f.format(x).replace(".", ",")

    lines = [f"# Investigación de variantes pre-registradas — {stamp}", "",
             f"Periodo {start} → {end_ts.date()}; mismas reglas de costes y riesgo que la v1. Hipótesis y "
             "criterios fijados antes de ejecutar: `research/hypotheses.md`. "
             f"Pruebas totales N = {N_TRIALS} (para el Sharpe deflactado).", "",
             "| Variante | Rentab. total | Sharpe | Caída máx. | Operaciones | R medio (IC 95 %) | Sharpe deflactado | 2021–23 | 2024–26 | Cumple |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name, e in results.items():
        s = e["stats"]
        ci = e["avg_r_ci95"]
        passes = "—" if name == "v1" else ("✅ sí" if e["passes"] else "❌ no")
        lines.append(f"| {e['label']} | {pc(s.get('total_return'))} | {nm(s.get('sharpe'))} | {pc(s.get('max_drawdown'))} | "
                     f"{e['n_trades']} | {nm(e['avg_r'], '{:+.3f}')} ({nm(ci[0], '{:+.3f}')} a {nm(ci[1], '{:+.3f}')}) | "
                     f"{nm(e['dsr'], '{:.0%}')} | {pc(e['halves'][0])} | {pc(e['halves'][1])} | {passes} |")
    lines.append(f"| BTC comprar y mantener | {pc(btc_stats.get('total_return'))} | {nm(btc_stats.get('sharpe'))} | "
                 f"{pc(btc_stats.get('max_drawdown'))} | — | — | — | — | — | — |")
    lines += ["", "## Criterios por variante", ""]
    for name, e in results.items():
        if name == "v1":
            continue
        lines.append(f"### {e['label']}" + (f" ({e['notes']})" if e["notes"] else ""))
        for k, ok in e["criteria"].items():
            lines.append(f"- {'✅' if ok else '❌'} {k}")
        if "random" in e:
            rnd = e["random"]
            lines.append(f"- Frente a {rnd['runs']} carteras aleatorias: percentil {rnd['pct_rank_sharpe']:.0%} en Sharpe "
                         f"(mediana aleatoria: Sharpe {nm(rnd['sharpe_median'])}, rentabilidad {pc(rnd['total_return_median'])})")
        lines.append(f"- Salidas: {e['exits']} · tiempo invertido {pc(e['exposure'])} · aciertos {pc(e['win_rate'])}")
        lines.append("")
    passing = [e["label"] for n, e in results.items() if n != "v1" and e["passes"]]
    lines += ["## Conclusión", ""]
    if passing:
        lines.append("Cumple todos los criterios pre-registrados: " + ", ".join(passing) + ". Siguiente paso: "
                     "añadirla como cartera **simulada** en el sistema diario; el dinero real sigue desactivado.")
    else:
        lines.append("**Ninguna variante cumple todos los criterios pre-registrados.** No hay evidencia suficiente "
                     "para operar ninguna de ellas con dinero real. El sistema sigue como laboratorio en simulación.")
    lines += ["", f"Desviación de los Sharpe diarios entre pruebas (para el Sharpe deflactado): {sr_std:.4f}."]
    text = "\n".join(lines) + "\n"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"research_{stamp}.md"
    path.write_text(text, encoding="utf-8")
    slim = {n: {k: v for k, v in e.items() if k not in ("rets",)} for n, e in results.items()}
    (REPORTS_DIR / "research_latest.json").write_text(json.dumps(slim, indent=2, ensure_ascii=False, default=str))
    exp = REPORTS_DIR / "experiments.csv"
    rows = [{"date": stamp, "variant": f"{n} ({e['label']})", "period": f"{start}→{end_ts.date()}",
             "total_return": e["stats"].get("total_return"), "sharpe": e["stats"].get("sharpe"),
             "avg_r": e["avg_r"], "n_trades": e["n_trades"]} for n, e in results.items() if n != "v1"]
    pd.DataFrame(rows).to_csv(exp, mode="a", header=not exp.exists(), index=False)
    return str(path)
