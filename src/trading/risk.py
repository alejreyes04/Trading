"""Motor de riesgo: convierte candidatas ordenadas en entradas dimensionadas respetando límites duros.

Las decisiones del agente LLM solo pueden reducir el tamaño (size_mult ≤ 1) o vetar; nunca ampliar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd

from trading.config import Settings
from trading.ledger import Ledger
from trading.orders import MarketRules, round_price, round_qty


@dataclass
class Candidate:
    base: str
    market: str
    binance_symbol: str
    score: float
    ask_eur: float
    atr_pct: float
    slippage: float
    rules: MarketRules
    size_mult: float = 1.0
    llm_prob: float | None = None


@dataclass
class PlannedEntry:
    candidate: Candidate
    entry_limit: float
    stop_price: float
    stop_limit_price: float
    tp_price: float
    qty: float
    notional_eur: float
    risk_eur: float


@dataclass
class Plan:
    entries: list[PlannedEntry] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)   # (base, motivo)
    blocked_reason: str | None = None


def trade_levels(entry: float, atr_pct: float, settings: Settings, rules: MarketRules) -> tuple[float, float, float]:
    t = settings.trade
    stop = round_price(entry * (1 - t.stop_atr * atr_pct), rules.tick_size, up=False)
    stop_limit = round_price(stop * (1 - settings.exchange.stop_limit_offset), rules.tick_size, up=False)
    tp = round_price(entry * (1 + t.take_profit_atr * atr_pct), rules.tick_size, up=True)
    return stop, stop_limit, tp


def plan_entries(candidates: list[Candidate], ledger: Ledger, capital: float, marks: dict[str, float],
                 regime_multiplier: float, settings: Settings, returns: pd.DataFrame | None = None,
                 now: datetime | None = None) -> Plan:
    now = now or datetime.now(timezone.utc)
    r = settings.risk
    plan = Plan()
    equity = ledger.equity(capital, marks)
    peak = ledger.peak_equity(capital)
    if regime_multiplier <= 0:
        plan.blocked_reason = "régimen risk-off: no se abren posiciones nuevas"
        return plan
    if peak > 0 and equity < peak * (1 - r.max_drawdown_pause):
        plan.blocked_reason = (f"caída desde máximos {equity / peak - 1:.1%} supera el límite "
                               f"({r.max_drawdown_pause:.0%}): pausa de entradas")
        return plan

    open_trades = ledger.open_trades()
    held = {t.base for t in open_trades}
    held_symbols = [t.binance_symbol for t in open_trades]
    slots = r.max_positions - len(open_trades)
    open_risk = ledger.open_risk()
    cash = ledger.cash(capital)
    cooldown = timedelta(days=settings.trade.reentry_cooldown_days)

    for c in candidates:
        if slots <= 0:
            plan.rejected.append((c.base, "sin huecos (máximo de posiciones)"))
            continue
        if c.size_mult <= 0:
            plan.rejected.append((c.base, "vetada por el agente"))
            continue
        if c.base in held:
            plan.rejected.append((c.base, "ya hay posición abierta"))
            continue
        last = ledger.last_closed_at(c.base)
        if last and now - datetime.fromisoformat(last) < cooldown:
            plan.rejected.append((c.base, "periodo de espera tras cerrar"))
            continue
        if returns is not None and held_symbols:
            corr = max((_corr(returns, c.binance_symbol, h) for h in held_symbols), default=float("nan"))
            if math.isfinite(corr) and corr > settings.signals.max_correlation:
                plan.rejected.append((c.base, f"muy correlacionada con una posición abierta ({corr:.2f})"))
                continue

        entry = c.ask_eur * (1 + settings.exchange.entry_limit_buffer)
        entry = round_price(entry, c.rules.tick_size, up=True)
        stop, stop_limit, tp = trade_levels(entry, c.atr_pct, settings, c.rules)
        per_unit_risk = entry - stop
        if per_unit_risk <= 0:
            plan.rejected.append((c.base, "stop inválido"))
            continue
        risk_budget = equity * r.risk_per_trade * regime_multiplier * min(c.size_mult, 1.0)
        qty = risk_budget / per_unit_risk
        qty = min(qty, r.max_position_weight * equity / entry)
        fee = settings.exchange.taker_fee
        qty = min(qty, max(cash, 0) / (entry * (1 + fee)))
        qty = round_qty(qty, c.rules.quantity_decimals)
        notional = qty * entry
        risk_eur = qty * per_unit_risk
        if qty < c.rules.min_base or notional < c.rules.min_quote:
            plan.rejected.append((c.base, f"importe por debajo del mínimo de Bitvavo ({notional:.2f} €)"))
            continue
        if open_risk + risk_eur > r.max_open_risk * equity + 1e-9:
            plan.rejected.append((c.base, "superaría el riesgo total abierto permitido"))
            continue
        plan.entries.append(PlannedEntry(c, entry, stop, stop_limit, tp, qty, notional, risk_eur))
        slots -= 1
        open_risk += risk_eur
        cash -= notional * (1 + fee)
        held.add(c.base)
        held_symbols.append(c.binance_symbol)
    return plan


def _corr(returns: pd.DataFrame, a: str, b: str, window: int = 60) -> float:
    if a not in returns.columns or b not in returns.columns or a == b:
        return float("nan") if a != b else 1.0
    pair = returns[[a, b]].tail(window).dropna()
    return float(pair[a].corr(pair[b])) if len(pair) >= window // 2 else float("nan")
