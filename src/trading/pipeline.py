"""Orquestación de una ejecución: scan (cuant) → [agentes] → decide (riesgo + órdenes) → informe; y monitor."""

from __future__ import annotations

import hashlib
import json
import logging
import random
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from trading import features, report, scoring, universe
from trading.config import Settings, load_exclusions, load_settings
from trading.data import macro, rss
from trading.data.binance_vision import BinanceVision
from trading.data.bitvavo import Bitvavo, empty_candles
from trading.data.coingecko import CoinGecko
from trading.data.okx import OKX
from trading.ledger import Ledger, Trade, ensure_fills_template, load_meta, save_meta
from trading.orders import MarketRules
from trading.paper import iso, next_run_after, resolve_pending, update_exits
from trading.paths import RUNS_DIR
from trading.risk import Candidate, Plan, plan_entries
from trading.schemas import Context, Decisions, check_decisions_against

log = logging.getLogger(__name__)
BTC = features.BTC


# --------------------------------------------------------------------------- utilidades
def run_id_for(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%MZ")


def run_dir_for(now: datetime) -> Path:
    return RUNS_DIR / now.strftime("%Y/%m/%d/%H%M")


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=_json_default))


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    if isinstance(o, float) and not np.isfinite(o):
        return None
    raise TypeError(type(o))


def clean(x):
    """Sustituye NaN/inf por None para JSON."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [clean(v) for v in x]
    if isinstance(x, (float, np.floating)):
        return None if not np.isfinite(x) else round(float(x), 8)
    if isinstance(x, np.integer):
        return int(x)
    return x


class CandleCache:
    """Velas de 1 h de Bitvavo con caché por mercado durante la ejecución."""

    def __init__(self, bitvavo: Bitvavo):
        self.bitvavo = bitvavo
        self.cache: dict[str, pd.DataFrame] = {}

    def __call__(self, market: str, start: pd.Timestamp) -> pd.DataFrame:
        start = pd.Timestamp(start).floor("h")
        empty = empty_candles()
        if start >= pd.Timestamp.now(tz="UTC"):
            return empty
        cached = self.cache.get(market)
        if cached is None or cached.empty or cached.index.min() > start:
            # Bitvavo devuelve como máximo 1440 velas (60 días de 1 h), las más recientes desde `start`.
            # Las posiciones duran ≤ 10 días, así que una llamada basta.
            try:
                cached = self.bitvavo.candles(market, "1h", start_ms=int(start.timestamp() * 1000), limit=1440)
            except Exception as exc:
                log.warning("Velas de %s no disponibles: %s", market, exc)
                return empty
            cached = cached[~cached.index.duplicated()].sort_index()
            self.cache[market] = cached
        return cached[cached.index >= start]


def capital_for(portfolio: str, settings: Settings) -> float:
    return settings.capital.live_eur if portfolio == "live" else settings.capital.paper_eur


def open_paper_trades(plan: Plan, ledger: Ledger, run_id: str, now: datetime, settings: Settings,
                      asks: dict[str, float]) -> list[Trade]:
    """Ejecución simulada inmediata al ask actual + slippage (cartera simulada)."""
    out = []
    for e in plan.entries:
        c = e.candidate
        ask = asks.get(c.market, c.ask_eur)
        price = min(e.entry_limit, ask * (1 + c.slippage))
        t = _trade_from_plan(e, ledger.portfolio, run_id, now, settings)
        t.status, t.opened_at, t.entry_price = "open", iso(now), price
        t.notional_eur = price * e.qty
        t.risk_eur = max((price - e.stop_price) * e.qty, 1e-9)
        t.entry_fee_eur = round(t.notional_eur * settings.exchange.taker_fee, 6)
        t.last_checked_at = iso(now)
        ledger.trades.append(t)
        out.append(t)
    return out


def _trade_from_plan(e, portfolio: str, run_id: str, now: datetime, settings: Settings) -> Trade:
    c = e.candidate
    time_exit = now + pd.Timedelta(days=settings.trade.max_holding_days)
    return Trade(
        trade_id=f"{portfolio}-{run_id}-{c.base}", portfolio=portfolio, base=c.base, market=c.market,
        binance_symbol=c.binance_symbol, status="pending", run_id=run_id, proposed_at=iso(now), opened_at=None,
        entry_limit=e.entry_limit, entry_price=None, qty=e.qty, notional_eur=e.notional_eur,
        stop_price=e.stop_price, stop_limit_price=e.stop_limit_price, tp_price=e.tp_price,
        time_exit_at=iso(time_exit), expires_at=None, risk_eur=e.risk_eur, score=round(c.score, 4),
        size_mult=c.size_mult, llm_prob=c.llm_prob,
    )


def candidates_from(rows: list[dict], markets: pd.DataFrame, settings: Settings,
                    size: dict[str, float] | None = None, prob: dict[str, float] | None = None,
                    asks: dict[str, float] | None = None) -> list[Candidate]:
    out = []
    for r in rows:
        if r["market"] not in markets.index:
            continue
        out.append(Candidate(
            base=r["base"], market=r["market"], binance_symbol=r["binance_symbol"], score=r["score"],
            ask_eur=(asks or {}).get(r["market"], r["ask_eur"]), atr_pct=r["atr_pct"],
            slippage=scoring.slippage_for(r.get("qv_median_30") or np.nan, settings),
            rules=MarketRules.from_row(markets.loc[r["market"]]),
            size_mult=(size or {}).get(r["base"], 1.0), llm_prob=(prob or {}).get(r["base"]),
        ))
    return out


# --------------------------------------------------------------------------- SCAN
def scan(now: datetime | None = None, use_coingecko: bool = True) -> Path:
    settings, excl = load_settings(), load_exclusions()
    now = now or datetime.now(timezone.utc)
    rid, rdir = run_id_for(now), run_dir_for(now)
    rdir.mkdir(parents=True, exist_ok=True)
    ensure_fills_template()
    meta = load_meta()
    meta.setdefault("start_date", now.date().isoformat())
    save_meta(meta)

    bt, bv = Bitvavo(), BinanceVision()
    markets, tickers = bt.markets(), bt.tickers()
    marks = tickers["bid"].dropna().to_dict()
    asks = tickers["ask"].dropna().to_dict()
    fetch = CandleCache(bt)
    ledgers = {pf: Ledger(pf) for pf in ("Q", "QL", "RND", "live")}

    # 1) Mantenimiento: órdenes reales pendientes y salidas de posiciones abiertas.
    housekeeping = {"live_resolved": [t.trade_id for t in resolve_pending(ledgers["live"], fetch, settings, now)]}
    for pf, led in ledgers.items():
        housekeeping[f"{pf}_closed"] = [asdict(t) for t in update_exits(led, fetch, settings, now)]

    # 2) Universo, indicadores, régimen y puntuación.
    syms, tick24 = bv.spot_symbols(), bv.tickers_24h()
    cg, cgc = None, CoinGecko() if use_coingecko else None
    if cgc is not None:
        try:
            cg = cgc.markets()
        except Exception as exc:
            log.warning("CoinGecko no disponible: %s", exc)
    uni = universe.build(syms, tick24, markets, tickers, cg, settings, excl)
    static = uni[uni["eligible_static"]].index.tolist()
    held = {t.binance_symbol for led in ledgers.values() for t in led.open_trades()}
    frames = bv.klines_many(sorted(set(static) | held | {BTC}), limit=220)
    feats = features.compute(features.Panel.from_frames(frames), settings.trade.atr_period)
    snap = features.snapshot(feats)
    eligible = scoring.eligibility(snap, settings) & snap.index.isin(static)
    reg = scoring.regime(snap, eligible, settings)
    scored = scoring.score(snap, eligible, settings)
    info_cols = ["base", "bitvavo_market", "ask_eur", "bid_eur", "spread_bps", "bitvavo_vol_eur",
                 "market_cap", "fdv_mc", "mc_rank"]
    scored = scored.join(uni[[c for c in info_cols if c in uni.columns]], how="left")
    scored = scored.rename(columns={"bitvavo_market": "market"})
    scored["binance_symbol"] = scored.index
    cands = scored[scored["candidate"]]
    shortlist = cands.head(settings.signals.shortlist_size)

    # Posicionamiento en derivados (solo lista corta).
    funding = {}
    try:
        okx = OKX()
        swaps = okx.usdt_swaps()
        for base in shortlist["base"]:
            if f"{base}-USDT-SWAP" in swaps:
                funding[base] = okx.funding_rate(base)
    except Exception as exc:
        log.warning("OKX no disponible: %s", exc)

    # 3) Carteras automáticas: Q (solo cuant) y RND (aleatoria) se ejecutan ya.
    returns = feats["returns"].tail(90)
    rows = shortlist.reset_index(drop=True).to_dict("records")
    plans = {}
    q_plan = plan_entries(candidates_from(rows, markets, settings), ledgers["Q"], capital_for("Q", settings),
                          marks, reg.multiplier, settings, returns, now)
    open_paper_trades(q_plan, ledgers["Q"], rid, now, settings, asks)
    plans["Q"] = q_plan
    rng = random.Random(int(hashlib.sha256(rid.encode()).hexdigest(), 16) % 2**32)
    pool = scored.reset_index(drop=True).to_dict("records")
    pool = rng.sample(pool, min(len(pool), settings.signals.shortlist_size))  # misma nula que en el backtest
    rnd_plan = plan_entries(candidates_from(pool, markets, settings), ledgers["RND"],
                            capital_for("RND", settings), marks, reg.multiplier, settings, returns, now)
    open_paper_trades(rnd_plan, ledgers["RND"], rid, now, settings, asks)
    plans["RND"] = rnd_plan

    # 4) Contexto de mercado para los agentes.
    market = {"run_id": rid, "generated_at": iso(now), "regime": reg.to_dict(),
              "funnel": universe.funnel(uni), "n_eligible": int(eligible.sum()), "n_candidates": int(len(cands))}
    for key, fn in (("fear_greed", macro.fear_greed), ("macro", lambda: macro.fred_latest(settings.research.fred_series)),
                    ("headlines", lambda: rss.headlines(settings.research.rss_feeds))):
        try:
            market[key] = fn()
        except Exception as exc:
            market[key] = {"error": str(exc)[:200]}
    if cgc is not None:
        try:
            market["global"] = cgc.global_data()
            market["trending"] = cgc.trending()
        except Exception as exc:
            market["global"] = {"error": str(exc)[:200]}

    shortlist_out = []
    for r in rows:
        plan_preview = None
        cand = candidates_from([r], markets, settings)
        if cand:
            from trading.risk import trade_levels
            entry = cand[0].ask_eur * (1 + settings.exchange.entry_limit_buffer)
            stop, stop_limit, tp = trade_levels(entry, cand[0].atr_pct, settings, cand[0].rules)
            plan_preview = {"entry_ref_eur": entry, "stop_eur": stop, "tp_eur": tp,
                            "stop_pct": stop / entry - 1, "tp_pct": tp / entry - 1}
        news = rss.mentions(market["headlines"], [r["base"]]) if isinstance(market.get("headlines"), list) else []
        shortlist_out.append(clean({
            "symbol": r["base"], "market": r["market"], "binance_symbol": r["binance_symbol"],
            "rank": len(shortlist_out) + 1, "score": r["score"],
            "blocks": {k: r[f"b_{k}"] for k in ("trend", "relative", "participation", "entry_quality")},
            "metrics": {k: r.get(k) for k in ("ret_7", "ret_14", "ret_28", "mom_28_adj", "rs_btc_14",
                                                 "trend_quality_28", "vol_z", "overext_atr", "rsi_14",
                                                 "atr_pct", "vol_30_ann", "dist_high_28")},
            "liquidity": {k: r.get(k) for k in ("spread_bps", "bitvavo_vol_eur", "qv_median_30")},
            "fundamentals": {k: r.get(k) for k in ("market_cap", "fdv_mc", "mc_rank")},
            "funding_rate_8h": funding.get(r["base"]),
            "plan_preview": plan_preview,
            "headlines": news[:5],
        }))

    # 5) Guardar todo.
    keep = [c for c in scored.columns if c not in ("returns",)]
    scored[keep].to_parquet(rdir / "scan.parquet")
    uni.to_csv(rdir / "universe.csv")
    returns.to_parquet(rdir / "returns.parquet")
    write_json(rdir / "market.json", clean(market))
    write_json(rdir / "shortlist.json", shortlist_out)
    open_positions = [clean(asdict(t)) for led in ledgers.values() for t in led.open_trades()]
    write_json(rdir / "positions.json", open_positions)
    write_json(rdir / "housekeeping.json", clean(housekeeping))
    for pf in ("Q", "RND"):
        ledgers[pf].record_equity(rid, capital_for(pf, settings), marks)
    for led in ledgers.values():
        led.save()
    write_json(rdir / "plans_auto.json", clean({pf: _plan_summary(p) for pf, p in plans.items()}))
    (rdir / "agent_brief.md").write_text(report.agent_brief(market, shortlist_out, open_positions), encoding="utf-8")
    return rdir


def _plan_summary(plan: Plan) -> dict:
    return {"blocked_reason": plan.blocked_reason,
            "entries": [{"base": e.candidate.base, "qty": e.qty, "entry_limit": e.entry_limit,
                         "stop": e.stop_price, "tp": e.tp_price, "risk_eur": e.risk_eur} for e in plan.entries],
            "rejected": [{"base": b, "reason": r} for b, r in plan.rejected]}


# --------------------------------------------------------------------------- DECIDE
def load_agent_outputs(rdir: Path, shortlist: list[dict], open_ids: list[str]) -> tuple[Context | None, Decisions | None, list[str]]:
    notes = []
    ctx = dec = None
    if (rdir / "context.json").exists():
        try:
            ctx = Context.model_validate_json((rdir / "context.json").read_text())
        except Exception as exc:
            notes.append(f"context.json inválido, se ignora: {exc}")
    else:
        notes.append("sin context.json (agente investigador no ejecutado)")
    if (rdir / "decisions.json").exists():
        try:
            dec = Decisions.model_validate_json((rdir / "decisions.json").read_text())
            errors = check_decisions_against(dec, [s["symbol"] for s in shortlist], open_ids)
            if errors:
                notes.append("decisions.json incoherente, se ignora: " + "; ".join(errors))
                dec = None
        except Exception as exc:
            notes.append(f"decisions.json inválido, se ignora: {exc}")
    else:
        notes.append("sin decisions.json (agente trader no ejecutado)")
    return ctx, dec, notes


def decide(rdir: Path, now: datetime | None = None, force: bool = False, live: bool | None = None) -> dict:
    settings = load_settings()
    now = now or datetime.now(timezone.utc)
    if (rdir / "proposals.json").exists() and not force:
        raise SystemExit(f"{rdir} ya tiene proposals.json: decide ya se ejecutó para esta ejecución "
                         "(usa --force solo si sabes lo que haces; duplicaría operaciones)")
    shortlist = json.loads((rdir / "shortlist.json").read_text())
    market = json.loads((rdir / "market.json").read_text())
    rid = market["run_id"]
    ledgers = {pf: Ledger(pf) for pf in ("Q", "QL", "RND", "live")}
    open_ids = [t.trade_id for pf in ("QL", "live") for t in ledgers[pf].open_trades()]
    ctx, dec, notes = load_agent_outputs(rdir, shortlist, open_ids)

    size = {s["symbol"]: 1.0 for s in shortlist}
    prob: dict[str, float] = {}
    if dec:
        for e in dec.entries:
            size[e.symbol] = e.size_mult
            prob[e.symbol] = e.p_tp_before_sl
    else:
        notes.append("QL y propuestas reales usan solo la parte cuant (sin veto del agente)")
    hard_vetoes = {}
    if ctx:
        for sym, sc in ctx.symbols.items():
            for rf in sc.red_flags:
                if rf.type in ("hack", "delisting", "depeg") and rf.severity == "alta":
                    size[sym] = 0.0
                    hard_vetoes[sym] = f"{rf.type}: {rf.text}"

    bt = Bitvavo()
    markets, tickers = bt.markets(), bt.tickers()
    marks, asks = tickers["bid"].dropna().to_dict(), tickers["ask"].dropna().to_dict()
    returns = pd.read_parquet(rdir / "returns.parquet")
    reg_mult = market["regime"]["multiplier"]
    rows = [{"base": s["symbol"], "market": s["market"], "binance_symbol": s["binance_symbol"], "score": s["score"],
             "ask_eur": asks.get(s["market"], (s.get("plan_preview") or {}).get("entry_ref_eur")),
             "atr_pct": s["metrics"]["atr_pct"], "qv_median_30": s["liquidity"].get("qv_median_30")}
            for s in shortlist]

    ql_plan = plan_entries(candidates_from(rows, markets, settings, size, prob, asks), ledgers["QL"],
                           capital_for("QL", settings), marks, reg_mult, settings, returns, now)
    open_paper_trades(ql_plan, ledgers["QL"], rid, now, settings, asks)

    live_plan, proposals = None, []
    live_enabled = settings.mode.live_enabled if live is None else (live and settings.mode.live_enabled)
    if live_enabled:
        live_plan = plan_entries(candidates_from(rows, markets, settings, size, prob, asks), ledgers["live"],
                                 capital_for("live", settings), marks, reg_mult, settings, returns, now)
        expires = next_run_after(now, settings)
        for e in live_plan.entries:
            t = _trade_from_plan(e, "live", rid, now, settings)
            t.expires_at = iso(expires)
            ledgers["live"].trades.append(t)
            proposals.append(t)

    exit_actions = []
    if dec:
        for x in dec.exits:
            if x.action != "close":
                continue
            for pf in ("QL", "live"):
                for t in ledgers[pf].trades:
                    if t.trade_id == x.trade_id and t.status == "open":
                        price = marks.get(t.market, t.entry_price)
                        t.close(iso(now), price, "agente", settings.exchange.taker_fee)
                        exit_actions.append({"trade_id": t.trade_id, "portfolio": pf, "price": price, "reason": x.reason})
                    elif t.trade_id == x.trade_id and t.status == "pending":
                        t.status = "cancelled"
                        t.notes = (t.notes + f" cancelada por el agente: {x.reason}").strip()

    equity = {pf: ledgers[pf].record_equity(rid, capital_for(pf, settings), marks) for pf in ("QL", "live")}
    for led in ledgers.values():
        led.save()
    result = {
        "run_id": rid, "notes": notes, "hard_vetoes": hard_vetoes,
        "ql": _plan_summary(ql_plan),
        "live": _plan_summary(live_plan) if live_plan else {"blocked_reason": "modo real desactivado en esta ejecución"},
        "proposals": [asdict(t) for t in proposals], "exit_actions": exit_actions, "equity": equity,
    }
    write_json(rdir / "proposals.json", clean(result))
    report.write_main_report(rdir, market, shortlist, ctx, dec, result, ledgers, settings)
    return result


# --------------------------------------------------------------------------- MONITOR
def monitor(now: datetime | None = None) -> tuple[Path, bool]:
    settings = load_settings()
    now = now or datetime.now(timezone.utc)
    rid, rdir = run_id_for(now), run_dir_for(now)
    rdir.mkdir(parents=True, exist_ok=True)
    bt = Bitvavo()
    tickers = bt.tickers()
    marks = tickers["bid"].dropna().to_dict()
    fetch = CandleCache(bt)
    ledgers = {pf: Ledger(pf) for pf in ("Q", "QL", "RND", "live")}
    resolved = resolve_pending(ledgers["live"], fetch, settings, now)
    closed = {pf: update_exits(led, fetch, settings, now) for pf, led in ledgers.items()}
    try:
        heads = rss.headlines(settings.research.rss_feeds, hours=12)
    except Exception:
        heads = []
    alerts = []
    for t in ledgers["live"].open_trades() + ledgers["QL"].open_trades():
        hits = rss.mentions(heads, [t.base])
        if hits:
            alerts.append({"trade_id": t.trade_id, "base": t.base, "headlines": hits[:5]})
    soon = []
    for t in ledgers["live"].trades:
        if t.status == "open":
            hours_left = (datetime.fromisoformat(t.time_exit_at) - now).total_seconds() / 3600
            if hours_left <= 14:
                soon.append({"trade_id": t.trade_id, "base": t.base, "time_exit_at": t.time_exit_at,
                             "bid": marks.get(t.market)})
    for pf, led in ledgers.items():
        led.record_equity(rid, capital_for(pf, settings), marks)
        led.save()
    data = {"run_id": rid, "generated_at": iso(now), "resolved_live": [asdict(t) for t in resolved],
            "closed": {pf: [asdict(t) for t in ts] for pf, ts in closed.items()},
            "news_alerts": alerts, "time_exits_soon": soon}
    write_json(rdir / "monitor.json", clean(data))
    needs_action = bool(resolved or closed["live"] or alerts or soon)
    report.write_monitor_report(rdir, data, ledgers, settings, marks)
    return rdir, needs_action
