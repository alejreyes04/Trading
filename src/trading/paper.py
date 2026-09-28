"""Simulación de ejecuciones: entradas, salidas por stop/objetivo/tiempo y resolución de órdenes reales pendientes.

Reglas prudentes (iguales en backtest y en real):
  * Si una vela toca el stop y el objetivo a la vez, se asume el STOP.
  * Si la vela abre por debajo del stop (hueco), la salida es la apertura, no el stop.
  * El stop se ejecuta con slippage extra; el objetivo es una orden límite (comisión maker).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from trading.config import Settings
from trading.ledger import Ledger, Trade, load_fills


def iso(dt: datetime | pd.Timestamp) -> str:
    return pd.Timestamp(dt).tz_convert("UTC").to_pydatetime().replace(microsecond=0).isoformat()


def check_exit(trade: Trade, candles: pd.DataFrame, settings: Settings) -> tuple[str, float, str] | None:
    """Recorre velas (ordenadas) desde la entrada. Devuelve (fecha, precio, motivo) o None."""
    time_exit = pd.Timestamp(trade.time_exit_at)
    for ts, c in candles.iterrows():
        if ts >= time_exit:
            return iso(ts), float(c["open"]), "tiempo"
        if c["low"] <= trade.stop_price:
            fill = min(trade.stop_price, float(c["open"])) * (1 - settings.costs.stop_extra_slippage)
            return iso(ts), fill, "stop"
        if c["high"] >= trade.tp_price:
            fill = max(trade.tp_price, float(c["open"])) if c["open"] > trade.tp_price else trade.tp_price
            return iso(ts), fill, "objetivo"
    return None


def fee_for(reason: str, settings: Settings) -> float:
    return settings.exchange.maker_fee if reason == "objetivo" else settings.exchange.taker_fee


def update_exits(ledger: Ledger, candle_fetcher, settings: Settings, now: datetime) -> list[Trade]:
    """Comprueba salidas de las posiciones abiertas con velas de 1 h desde la última revisión."""
    closed = []
    for t in [x for x in ledger.trades if x.status == "open"]:
        start = pd.Timestamp(t.last_checked_at or t.opened_at)
        candles = candle_fetcher(t.market, start)
        if candles is None or candles.empty:
            continue
        candles = candles[candles.index >= start]
        # Solo velas que empezaron después de la entrada (no sabemos qué pasó antes dentro de la vela).
        candles = candles[candles.index >= pd.Timestamp(t.opened_at).ceil("h")]
        result = check_exit(t, candles, settings)
        if result is None and now >= datetime.fromisoformat(t.time_exit_at) and not candles.empty:
            result = (iso(candles.index[-1]), float(candles["close"].iloc[-1]), "tiempo")
        if result:
            when, price, reason = result
            t.close(when, price, reason, fee_for(reason, settings))
            closed.append(t)
        elif not candles.empty:
            t.last_checked_at = iso(candles.index[-1])
    return closed


def resolve_pending(ledger: Ledger, candle_fetcher, settings: Settings, now: datetime) -> list[Trade]:
    """Órdenes reales pendientes: aplica tus correcciones (fills.csv) o, si no hay, asume ejecución
    al precio límite si el mercado lo tocó antes de caducar; si no, se da por no ejecutada."""
    fills = load_fills(ledger.dir.parent)
    by_id = {r["trade_id"]: r for r in fills.to_dict("records")} if not fills.empty else {}
    changed = []
    for t in ledger.trades:
        fix = by_id.get(t.trade_id)
        if fix:
            if _apply_fix(t, fix, settings):
                changed.append(t)
            continue
        if t.status != "pending":
            continue
        # Incluye la vela de la hora de la propuesta: el límite se fija por encima del ask de ese momento.
        start = pd.Timestamp(t.proposed_at).floor("h")
        candles = candle_fetcher(t.market, start)
        expires = pd.Timestamp(t.expires_at) if t.expires_at else pd.Timestamp(now)
        window = candles[(candles.index >= start) & (candles.index < expires)] if candles is not None else None
        touched = window[window["low"] <= t.entry_limit] if window is not None and not window.empty else None
        if touched is not None and not touched.empty:
            t.status = "open"
            t.opened_at = iso(max(touched.index[0], pd.Timestamp(t.proposed_at)))
            t.entry_price = t.entry_limit
            t.entry_fee_eur = round(t.entry_limit * t.qty * settings.exchange.taker_fee, 6)
            t.notes = (t.notes + " ejecución asumida (precio tocó el límite)").strip()
            changed.append(t)
        elif pd.Timestamp(now) >= expires:
            t.status = "cancelled"
            t.notes = (t.notes + " caducada sin tocar el límite").strip()
            changed.append(t)
    return changed


def _apply_fix(t: Trade, fix: dict, settings: Settings) -> bool:
    estado = (fix.get("estado") or "").strip().lower()
    if estado == "no_ejecutada" and t.status in ("pending", "open") and not t.closed_at:
        t.status = "cancelled"
        t.notes = (t.notes + " no ejecutada (según fills.csv)").strip()
        return True
    if estado in ("ejecutada", "cerrada") and t.status in ("pending", "open", "cancelled"):
        if fix.get("precio_entrada"):
            t.entry_price = float(fix["precio_entrada"].replace(",", "."))
        else:
            t.entry_price = t.entry_price or t.entry_limit
        if fix.get("cantidad"):
            t.qty = float(fix["cantidad"].replace(",", "."))
        t.notional_eur = t.entry_price * t.qty
        t.risk_eur = max((t.entry_price - t.stop_price) * t.qty, 1e-9)
        t.entry_fee_eur = round(t.notional_eur * settings.exchange.taker_fee, 6)
        t.opened_at = t.opened_at or t.proposed_at
        t.status = "open"
        if estado == "cerrada" and fix.get("precio_salida"):
            when = fix.get("fecha_salida") or datetime.now(timezone.utc).isoformat()
            t.close(when, float(fix["precio_salida"].replace(",", ".")), "manual", settings.exchange.taker_fee)
        return True
    return False


def next_run_after(now: datetime, settings: Settings) -> datetime:
    """Siguiente ejecución programada (para la caducidad de las órdenes límite)."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(settings.schedule.timezone)
    local = now.astimezone(tz)
    times = sorted([settings.schedule.main_run] + settings.schedule.monitor_runs)
    for day in range(0, 2):
        for hhmm in times:
            h, m = map(int, hhmm.split(":"))
            cand = (local + timedelta(days=day)).replace(hour=h, minute=m, second=0, microsecond=0)
            if cand > local + timedelta(minutes=30):
                return cand.astimezone(timezone.utc)
    return now + timedelta(hours=8)
