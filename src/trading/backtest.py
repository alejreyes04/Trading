"""Backtest diario de la parte cuantitativa, con la MISMA lógica que en real:
features.compute → scoring (elegibilidad, régimen, puntuación) → risk.plan_entries → salidas de paper.check_exit.

Supuestos (prudentes y explícitos):
  * Señal al cierre del día t (UTC); entrada a la apertura de t+1 + slippage según liquidez + comisión taker.
  * Salidas con velas diarias: stop antes que objetivo si ambos se tocan el mismo día; hueco → apertura.
  * Stop con slippage extra; objetivo como límite (comisión maker); salida por tiempo al cierre del día 10.
  * Universo punto-en-el-tiempo: pares USDT de Binance que existían ese día (incluye retirados).
  * Precios en USDT de Binance como aproximación a EUR de Bitvavo (se ignora el tipo de cambio).
  * Par que deja de cotizar 3 días seguidos: salida al último cierre −5 % (retirada de mercado).
"""

from __future__ import annotations

import json
import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from trading import features, scoring
from trading.config import Settings, load_exclusions, load_settings
from trading.data.binance_archive import download
from trading.evaluate import bootstrap_mean_ci, equity_stats, probabilistic_sharpe
from trading.orders import MarketRules
from trading.paths import REPORTS_DIR
from trading.risk import Candidate, plan_entries

log = logging.getLogger(__name__)
NO_RULES = MarketRules(tick_size=0.0, quantity_decimals=12, min_quote=0.0, min_base=0.0)


@dataclass
class Pos:
    base: str
    market: str
    binance_symbol: str
    qty: float
    entry: float
    stop: float
    tp: float
    entry_date: pd.Timestamp
    risk_eur: float
    fee_in: float
    regime: str
    slippage: float
    bars: int = 0
    missing: int = 0
    last_close: float = float("nan")


@dataclass
class Book:
    """Cartera simulada con la misma interfaz que Ledger (la que usa risk.plan_entries)."""
    capital: float
    cash_: float = 0.0
    positions: dict[str, Pos] = field(default_factory=dict)
    trades: list[dict] = field(default_factory=list)
    peak: float = 0.0
    last_exit: dict[str, str] = field(default_factory=dict)
    fees: float = 0.0

    def __post_init__(self):
        self.cash_ = self.capital
        self.peak = self.capital

    # --- interfaz Ledger ---
    def equity(self, capital: float, marks: dict[str, float]) -> float:
        return self.cash_ + sum(p.qty * marks.get(p.market, p.last_close if math.isfinite(p.last_close) else p.entry)
                                for p in self.positions.values())

    def cash(self, capital: float) -> float:
        return self.cash_

    def open_trades(self) -> list[Pos]:
        return list(self.positions.values())

    def open_risk(self) -> float:
        return sum(p.risk_eur for p in self.positions.values())

    def peak_equity(self, capital: float) -> float:
        return self.peak

    def last_closed_at(self, base: str) -> str | None:
        return self.last_exit.get(base)

    # --- operaciones ---
    def close(self, p: Pos, date: pd.Timestamp, price: float, reason: str, fee_rate: float) -> None:
        fee = price * p.qty * fee_rate
        self.cash_ += price * p.qty - fee
        self.fees += fee
        pnl = (price - p.entry) * p.qty - p.fee_in - fee
        self.trades.append({"symbol": p.binance_symbol, "entry_date": p.entry_date.date().isoformat(),
                            "exit_date": date.date().isoformat(), "entry": p.entry, "exit": price, "reason": reason,
                            "pnl": pnl, "r": pnl / p.risk_eur if p.risk_eur > 0 else float("nan"),
                            "bars": p.bars, "regime": p.regime})
        self.last_exit[p.base] = date.to_pydatetime().isoformat()
        del self.positions[p.base]


def load_panel(start: str, refresh: bool) -> features.Panel:
    data = download(start, load_exclusions(), refresh=refresh)
    return features.Panel.from_frames(data)


def precompute(panel: features.Panel, settings: Settings, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    feats = features.compute(panel, settings.trade.atr_period)
    dates = [d for d in feats["close"].index if start <= d <= end]
    daily = {}
    for d in dates:
        snap = features.snapshot(feats, d)
        elig = scoring.eligibility(snap, settings)
        if features.BTC not in snap.index or not elig.any():
            continue
        reg = scoring.regime(snap, elig, settings)
        scored = scoring.score(snap, elig, settings)
        cands = scored[scored["candidate"]].head(settings.signals.shortlist_size)
        daily[d] = {
            "regime": reg.label, "mult": reg.multiplier, "breadth": reg.breadth,
            "eligible": scored.index.tolist(),
            "candidates": cands.index.tolist(),
            "score": scored["score"].to_dict(), "atr_pct": scored["atr_pct"].to_dict(),
            "qv": scored["qv_median_30"].to_dict(), "close": scored["close"].to_dict(),
        }
    return {"feats": feats, "daily": daily, "dates": dates}


def simulate(pre: dict, panel: features.Panel, settings: Settings, mode: str = "Q", seed: int = 0) -> dict:
    t, r = settings.trade, settings.risk
    book = Book(settings.capital.paper_eur)
    rng = random.Random(seed)
    opens, highs, lows, closes = panel.open, panel.high, panel.low, panel.close
    returns = pre["feats"]["returns"]
    dates = pre["dates"]
    pending: list[tuple] = []
    curve, exposure_days = [], 0
    for i, d in enumerate(dates):
        # 1) ejecutar entradas planificadas ayer, a la apertura de hoy
        for entry in pending:
            sym, qty, atr_pct, slip, regime_label = entry
            o = opens.at[d, sym] if sym in opens.columns else float("nan")
            if not math.isfinite(o) or o <= 0:
                continue
            price = o * (1 + slip)
            fee_rate = settings.exchange.taker_fee
            qty = min(qty, book.cash_ / (price * (1 + fee_rate)))
            if qty <= 0:
                continue
            fee = price * qty * fee_rate
            book.cash_ -= price * qty + fee
            book.fees += fee
            stop = price * (1 - t.stop_atr * atr_pct)
            tp = price * (1 + t.take_profit_atr * atr_pct)
            book.positions[sym] = Pos(sym, sym, sym, qty, price, stop, tp, d, qty * (price - stop), fee,
                                      regime_label, slip)
        pending = []

        # 2) salidas con la vela de hoy
        for p in list(book.positions.values()):
            o, h, lo, c = (opens.at[d, p.market], highs.at[d, p.market], lows.at[d, p.market], closes.at[d, p.market])
            if not all(math.isfinite(x) for x in (o, h, lo, c)):
                p.missing += 1
                if p.missing >= 3:
                    book.close(p, d, p.last_close * 0.95, "retirada", settings.exchange.taker_fee)
                continue
            p.missing = 0
            p.bars += 1
            p.last_close = c
            if lo <= p.stop:
                fill = min(p.stop, o) * (1 - settings.costs.stop_extra_slippage)
                book.close(p, d, fill, "stop", settings.exchange.taker_fee)
            elif h >= p.tp:
                book.close(p, d, max(p.tp, o), "objetivo", settings.exchange.maker_fee)
            elif p.bars >= t.max_holding_days:
                book.close(p, d, c * (1 - p.slippage), "tiempo", settings.exchange.taker_fee)

        marks = {p.market: p.last_close for p in book.positions.values()}
        eq = book.equity(book.capital, marks)
        book.peak = max(book.peak, eq)
        curve.append((d, eq))
        exposure_days += bool(book.positions)

        # 3) señales al cierre de hoy → entradas de mañana
        day = pre["daily"].get(d)
        if day is None or i == len(dates) - 1:
            continue
        if mode == "Q":
            pool = day["candidates"]
        else:
            # Nula justa: el mismo nº de candidatas que la lista corta, elegidas al azar del universo elegible.
            pool = rng.sample(day["eligible"], min(len(day["eligible"]), settings.signals.shortlist_size))
        cands = [Candidate(base=s, market=s, binance_symbol=s, score=day["score"][s], ask_eur=day["close"][s],
                           atr_pct=day["atr_pct"][s], slippage=scoring.slippage_for(day["qv"][s], settings),
                           rules=NO_RULES) for s in pool]
        rets_window = returns.iloc[max(0, returns.index.get_loc(d) - 60): returns.index.get_loc(d) + 1]
        plan = plan_entries(cands, book, book.capital, marks, day["mult"], settings, rets_window,
                            d.to_pydatetime())
        pending = [(e.candidate.market, e.qty, e.candidate.atr_pct, e.candidate.slippage, day["regime"])
                   for e in plan.entries]

    eq = pd.Series(dict(curve))
    return {"equity": eq, "trades": pd.DataFrame(book.trades), "fees": book.fees,
            "exposure": exposure_days / max(len(dates), 1)}


def benchmarks(pre: dict, panel: features.Panel, settings: Settings) -> dict[str, pd.Series]:
    dates = pre["dates"]
    btc = panel.close[features.BTC].reindex(dates)
    out = {"BTC comprar y mantener": settings.capital.paper_eur * btc / btc.iloc[0]}
    # Cesta igual ponderada del top-20 por volumen, rebalanceo mensual con costes.
    rets = panel.close.pct_change(fill_method=None)
    eq, value, members, month = [], settings.capital.paper_eur, [], None
    cost = settings.exchange.taker_fee + settings.costs.slippage_mid_liquidity
    for d in dates:
        if members:
            day_ret = rets.loc[d, members].fillna(0).mean()
            value *= 1 + day_ret
        if d.month != month and d in pre["daily"]:
            month = d.month
            day = pre["daily"][d]
            top = sorted(day["eligible"], key=lambda s: day["qv"][s], reverse=True)[:20]
            turnover = len(set(top) - set(members)) / 20 if members else 1.0
            value *= 1 - 2 * cost * turnover
            members = top
        eq.append(value)
    out["Cesta top-20 igual ponderada"] = pd.Series(eq, index=dates)
    return out


_CTX: dict = {}


def _random_worker(seed: int) -> dict:
    out = simulate(_CTX["pre"], _CTX["panel"], _CTX["settings"], "RND", seed=seed)
    return {"equity": out["equity"], "trades": out["trades"][["r", "pnl"]] if not out["trades"].empty else out["trades"]}


def run_random(pre: dict, panel: features.Panel, settings: Settings, runs: int, workers: int = 4) -> list[dict]:
    """Carteras aleatorias en paralelo (fork: los datos se comparten sin copiarlos)."""
    import multiprocessing as mp

    _CTX.update(pre=pre, panel=panel, settings=settings)
    ctx = mp.get_context("fork")
    with ctx.Pool(workers) as pool:
        return pool.map(_random_worker, range(runs))


def run_backtest(start: str = "2021-01-01", end: str | None = None, random_runs: int = 100,
                 refresh: bool = False) -> str:
    settings = load_settings()
    t0 = datetime.now(timezone.utc)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") if end else pd.Timestamp.now(tz="UTC")
    warmup = (start_ts - pd.Timedelta(days=260)).strftime("%Y-%m-%d")
    panel = load_panel(warmup, refresh)
    pre = precompute(panel, settings, start_ts, end_ts)
    res_q = simulate(pre, panel, settings, "Q")
    rnd = run_random(pre, panel, settings, random_runs)
    bench = benchmarks(pre, panel, settings)
    results = summarize(res_q, rnd, bench, pre, settings, start, end_ts, panel)
    results["runtime_s"] = round((datetime.now(timezone.utc) - t0).total_seconds(), 1)
    return write_report(results, res_q)


def summarize(res_q, rnd, bench, pre, settings, start, end_ts, panel) -> dict:
    eq = res_q["equity"]
    trades = res_q["trades"]
    q_stats = equity_stats(eq)
    rets = eq.pct_change().dropna()
    r = trades["r"].to_numpy() if not trades.empty else np.array([])
    lo, hi = bootstrap_mean_ci(r) if len(r) > 1 else (float("nan"), float("nan"))
    rnd_stats = [equity_stats(x["equity"]) for x in rnd]
    rnd_tot = np.array([s["total_return"] for s in rnd_stats])
    rnd_sharpe = np.array([s["sharpe"] for s in rnd_stats])
    rnd_avg_r = np.array([x["trades"]["r"].mean() if not x["trades"].empty else np.nan for x in rnd])
    yearly = {}
    series = {"Q (estrategia)": eq, **bench, "RND (mediana)": pd.concat([x["equity"] for x in rnd], axis=1).median(axis=1)}
    for name, s in series.items():
        s = s.dropna()
        yearly[name] = {str(y): float(g.iloc[-1] / g.iloc[0] - 1) for y, g in s.groupby(s.index.year) if len(g) > 5}
    by_regime = {}
    if not trades.empty:
        for reg, g in trades.groupby("regime"):
            by_regime[reg] = {"n": int(len(g)), "avg_r": float(g["r"].mean()), "win_rate": float((g["pnl"] > 0).mean())}
    by_reason = trades["reason"].value_counts().to_dict() if not trades.empty else {}
    regime_days = pd.Series([d["regime"] for d in pre["daily"].values()]).value_counts(normalize=True).to_dict()
    n_symbols = int(sum(1 for c in panel.close.columns if panel.close[c].loc[pre["dates"][0]:].notna().any()))
    return {
        "period": [start, end_ts.date().isoformat()], "n_days": len(pre["dates"]), "n_symbols_seen": n_symbols,
        "settings": settings.model_dump(),
        "strategy": {**q_stats, "n_trades": int(len(trades)), "win_rate": float((trades["pnl"] > 0).mean()) if len(trades) else None,
                     "avg_r": float(np.mean(r)) if len(r) else None, "avg_r_ci95": [lo, hi],
                     "profit_factor": float(trades.loc[trades.pnl > 0, "pnl"].sum() / -trades.loc[trades.pnl <= 0, "pnl"].sum())
                     if len(trades) and (trades.pnl <= 0).any() else None,
                     "avg_bars": float(trades["bars"].mean()) if len(trades) else None,
                     "fees_pct_capital": res_q["fees"] / settings.capital.paper_eur, "exposure": res_q["exposure"],
                     "psr_vs_0": probabilistic_sharpe(rets), "exits": by_reason},
        "benchmarks": {name: equity_stats(s) for name, s in bench.items()},
        "random": {"runs": len(rnd), "total_return_median": float(np.nanmedian(rnd_tot)),
                   "total_return_p5": float(np.nanpercentile(rnd_tot, 5)), "total_return_p95": float(np.nanpercentile(rnd_tot, 95)),
                   "sharpe_median": float(np.nanmedian(rnd_sharpe)), "avg_r_median": float(np.nanmedian(rnd_avg_r)),
                   "strategy_pct_rank_total_return": float((rnd_tot < q_stats.get("total_return", np.nan)).mean()),
                   "strategy_pct_rank_sharpe": float((rnd_sharpe < q_stats.get("sharpe", np.nan)).mean()),
                   "strategy_pct_rank_avg_r": float((rnd_avg_r < (np.mean(r) if len(r) else np.nan)).mean())},
        "yearly": yearly, "by_regime": by_regime, "regime_days": regime_days,
        "variants_tested": 1,
    }


def write_report(res: dict, res_q: dict) -> str:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (REPORTS_DIR / "backtest_latest.json").write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    res_q["trades"].to_csv(REPORTS_DIR / "backtest_trades.csv", index=False)
    res_q["equity"].rename("equity").to_csv(REPORTS_DIR / "backtest_equity.csv")
    s, rnd = res["strategy"], res["random"]

    def pc(x):
        return "—" if x is None or (isinstance(x, float) and not math.isfinite(x)) else f"{x * 100:+.1f} %"

    def num(x, f="{:.2f}"):
        return "—" if x is None or (isinstance(x, float) and not math.isfinite(x)) else f.format(x)

    lines = [f"# Backtest de la parte cuantitativa — {stamp}", "",
             f"Periodo {res['period'][0]} → {res['period'][1]} ({res['n_days']} días) · {res['n_symbols_seen']} pares USDT "
             f"de Binance vistos (incluye retirados) · capital inicial {res['settings']['capital']['paper_eur']} · "
             f"reglas fijadas a priori (1 variante probada).", "",
             "Costes: comisión taker Bitvavo 0,25 % (maker 0,15 % en objetivos) + slippage 0,05–0,20 % según liquidez "
             "+ 0,5 % extra en stops.", "",
             "## Resultado global", "",
             "| Serie | Rentab. total | CAGR | Volatilidad | Sharpe | Sortino | Caída máx. |",
             "|---|---|---|---|---|---|---|",
             f"| **Q (estrategia)** | {pc(s.get('total_return'))} | {pc(s.get('cagr'))} | {pc(s.get('vol_ann'))} | "
             f"{num(s.get('sharpe'))} | {num(s.get('sortino'))} | {pc(s.get('max_drawdown'))} |"]
    for name, b in res["benchmarks"].items():
        lines.append(f"| {name} | {pc(b.get('total_return'))} | {pc(b.get('cagr'))} | {pc(b.get('vol_ann'))} | "
                     f"{num(b.get('sharpe'))} | {num(b.get('sortino'))} | {pc(b.get('max_drawdown'))} |")
    lines.append(f"| RND (mediana de {rnd['runs']} carteras aleatorias) | {pc(rnd['total_return_median'])} | — | — | "
                 f"{num(rnd['sharpe_median'])} | — | — |")
    lines += ["", "## Operaciones", "",
              f"- Operaciones: {s['n_trades']} · aciertos {pc(s['win_rate']) if s['win_rate'] is not None else '—'} · "
              f"R medio {num(s['avg_r'], '{:+.3f}')} (IC 95 % {num(s['avg_r_ci95'][0], '{:+.3f}')} a "
              f"{num(s['avg_r_ci95'][1], '{:+.3f}')}) · profit factor {num(s['profit_factor'])}",
              f"- Duración media {num(s['avg_bars'], '{:.1f}')} días · tiempo invertido {pc(s['exposure'])} de los días · "
              f"comisiones pagadas {pc(s['fees_pct_capital'])} del capital inicial",
              f"- Salidas: {s['exits']}",
              f"- Probabilidad de que el Sharpe real sea > 0 (PSR): {num(s['psr_vs_0'], '{:.1%}')}",
              f"- Frente a {rnd['runs']} carteras aleatorias con las mismas reglas: la estrategia supera al "
              f"{num(rnd['strategy_pct_rank_total_return'], '{:.0%}')} en rentabilidad, al "
              f"{num(rnd['strategy_pct_rank_sharpe'], '{:.0%}')} en Sharpe y al "
              f"{num(rnd['strategy_pct_rank_avg_r'], '{:.0%}')} en R medio "
              f"(rango aleatorio p5–p95 de rentabilidad: {pc(rnd['total_return_p5'])} a {pc(rnd['total_return_p95'])}).",
              "", "## Por año", "", "| Año | " + " | ".join(res["yearly"].keys()) + " |",
              "|---|" + "---|" * len(res["yearly"])]
    years = sorted({y for v in res["yearly"].values() for y in v})
    for y in years:
        lines.append(f"| {y} | " + " | ".join(pc(v.get(y)) for v in res["yearly"].values()) + " |")
    lines += ["", "## Por régimen de mercado (al abrir la operación)", "",
              f"Días en cada régimen: " + ", ".join(f"{k} {v:.0%}" for k, v in res["regime_days"].items()), "",
              "| Régimen | Operaciones | R medio | Aciertos |", "|---|---|---|---|"]
    for reg, v in res["by_regime"].items():
        lines.append(f"| {reg} | {v['n']} | {v['avg_r']:+.3f} | {v['win_rate']:.0%} |")
    lines += ["", "## Cómo leerlo", "",
              "- Si el intervalo de confianza del R medio incluye 0, **no hay evidencia estadística de ventaja**.",
              "- Superar a las carteras aleatorias indica que la *selección* aporta algo más allá de las reglas de "
              "salida y del régimen; no superarlas indica que lo que funcione se debe a estar invertido en mercado alcista.",
              "- El backtest no incluye al agente LLM (no se puede testear sin sesgo de anticipación).",
              f"- Tiempo de cálculo: {res.get('runtime_s')} s."]
    path = REPORTS_DIR / f"backtest_{stamp}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    exp = REPORTS_DIR / "experiments.csv"
    row = pd.DataFrame([{"date": stamp, "variant": "v1_a_priori", "period": "→".join(res["period"]),
                         "total_return": s.get("total_return"), "sharpe": s.get("sharpe"), "avg_r": s.get("avg_r"),
                         "n_trades": s.get("n_trades")}])
    row.to_csv(exp, mode="a", header=not exp.exists(), index=False)
    return str(path)
