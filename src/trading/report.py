"""Informes en español (Markdown) para ti y resumen breve para la notificación."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from trading.ledger import PORTFOLIO_NAMES, PORTFOLIOS
from trading.orders import fmt_eur, fmt_price, fmt_qty
from trading.paths import REPORTS_DIR

MADRID = ZoneInfo("Europe/Madrid")
REGIME_ES = {"risk_on": "ALCISTA (risk-on)", "neutral": "NEUTRAL", "risk_off": "BAJISTA (risk-off)"}
REASON_ES = {"stop": "stop-loss", "objetivo": "objetivo", "tiempo": "salida por tiempo (fecha máxima)",
             "tendencia": "fin de tendencia (cierre < SMA50)", "agente": "cierre propuesto por el agente",
             "manual": "cierre manual"}


def madrid(ts: str | None) -> str:
    if not ts:
        return "—"
    return datetime.fromisoformat(ts).astimezone(MADRID).strftime("%d/%m %H:%M")


def pct(x) -> str:
    return "—" if x is None else f"{x * 100:+.1f} %".replace(".", ",")


def _regime_line(market: dict) -> str:
    r = market["regime"]
    fg = market.get("fear_greed") or {}
    fg_txt = f" · Fear & Greed {fg.get('value')} ({fg.get('label')})" if "value" in fg else ""
    return (f"**Régimen:** {REGIME_ES[r['label']]} → exposición {r['multiplier']:.0%} · "
            f"BTC {r['btc_close']:,.0f} $ vs MA200 {r['btc_sma200']:,.0f} $ · "
            f"amplitud {r['breadth']:.0%} de {r['n_eligible']} monedas elegibles{fg_txt}").replace(",", ".")


# --------------------------------------------------------------------------- brief para agentes
def agent_brief(market: dict, shortlist: list[dict], positions: list[dict]) -> str:
    lines = [f"# Brief para agentes — run {market['run_id']}", "", _regime_line(market), ""]
    g = market.get("global") or {}
    if "btc_dominance_pct" in g:
        lines.append(f"Capitalización total {g['total_market_cap_usd'] / 1e12:.2f} billones de $ "
                     f"({g['market_cap_change_24h_pct']:+.1f} % 24 h) · dominancia BTC "
                     f"{g['btc_dominance_pct']:.1f} %".replace(".", ","))
    for m in market.get("macro") or []:
        if "value" in m:
            ch = f" (30 d: {m['change_30d']:+.2f})" if m.get("change_30d") is not None else ""
            lines.append(f"- {m['name']}: {m['value']}{ch} [{m['date']}]")
    if market.get("trending"):
        lines.append("- Trending CoinGecko (búsquedas): " + ", ".join(t["symbol"] for t in market["trending"][:15]))
    lines += ["", "## Lista corta (orden por puntuación cuant)", "",
              "| # | Moneda | Punt. | Ret 7d | Ret 28d | Sobreext. (ATR) | RSI | Stop | Objetivo | Funding 8h | Spread | FDV/MC |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in shortlist:
        m, p = s["metrics"], s.get("plan_preview") or {}
        fund = s.get("funding_rate_8h")
        fdv_mc = s["fundamentals"].get("fdv_mc")
        fdv_txt = "—" if fdv_mc is None else f"{fdv_mc:.2f}"
        fund_txt = "—" if fund is None else f"{fund * 100:.4f} %"
        lines.append(
            f"| {s['rank']} | {s['symbol']} | {s['score']:.3f} | {pct(m['ret_7'])} | {pct(m['ret_28'])} | "
            f"{m['overext_atr']:.2f} | {m['rsi_14']:.0f} | {pct(p.get('stop_pct'))} | {pct(p.get('tp_pct'))} | "
            f"{fund_txt} | {s['liquidity']['spread_bps']:.0f} pb | {fdv_txt} |")
    lines += ["", "## Titulares recientes (RSS, 36 h)", ""]
    heads = market.get("headlines") if isinstance(market.get("headlines"), list) else []
    for h in heads[:25]:
        lines.append(f"- [{h['source']}] {h['title']} — {h['link']}")
    lines += ["", "## Posiciones abiertas (para decisiones de salida)", ""]
    if not positions:
        lines.append("Ninguna.")
    for p in positions:
        if p["portfolio"] in ("QL", "live"):
            lines.append(f"- `{p['trade_id']}` {p['base']} ({p['status']}) entrada {p.get('entry_price') or p['entry_limit']} "
                         f"stop {p['stop_price']} objetivo {p['tp_price']} salida máx. {p['time_exit_at']}")
    lines += ["", "Sigue `agents/context_analyst.md` y después `agents/senior_trader.md`."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- informe principal
def order_block(t, rules_decimals: int | None = None) -> str:
    dec = rules_decimals if rules_decimals is not None else 8
    return "\n".join([
        f"#### COMPRAR {t.base} — mercado `{t.market}`",
        f"1. **Orden límite de compra:** {fmt_qty(t.qty, dec)} {t.base} a **{fmt_price(t.entry_limit)} €** "
        f"(≈ {fmt_eur(t.notional_eur)}). Válida hasta **{madrid(t.expires_at)}** (hora de Madrid); si no se "
        f"ejecuta, cancélala.",
        f"2. **Nada más ejecutarse, protege la posición** (OCO si tu app la ofrece):",
        f"   - Stop-loss límite: disparo **{fmt_price(t.stop_price)} €**, límite {fmt_price(t.stop_limit_price)} € "
        f"({pct(t.stop_price / t.entry_limit - 1)})",
        f"   - Objetivo (take-profit límite): **{fmt_price(t.tp_price)} €** ({pct(t.tp_price / t.entry_limit - 1)})",
        f"   - Si no puedes poner ambas, pon el **stop**; te aviso cuando toque el objetivo.",
        f"3. **Salida máxima:** {madrid(t.time_exit_at)}. Si sigue abierta, vende.",
        f"- Riesgo: **{fmt_eur(t.risk_eur)}** · puntuación {t.score:.3f}".replace("0.", "0,")
        + (f" · prob. objetivo antes que stop (agente): {t.llm_prob:.0%}" if t.llm_prob is not None else ""),
        f"- ID para `ledger/live/fills.csv`: `{t.trade_id}`",
    ])


def portfolio_table(ledgers: dict, settings, marks: dict) -> list[str]:
    from trading.pipeline import capital_for

    lines = ["| Cartera | Capital inicial | Capital actual | P&L | Cerradas | Aciertos | Abiertas |",
             "|---|---|---|---|---|---|---|"]
    for pf in PORTFOLIOS:
        led = ledgers[pf]
        cap = capital_for(pf, settings)
        eq = led.equity(cap, marks)
        closed = led.closed_trades()
        wins = sum(1 for t in closed if (t.pnl_eur or 0) > 0)
        lines.append(f"| {PORTFOLIO_NAMES[pf]} | {fmt_eur(cap)} | {fmt_eur(eq)} | {pct(eq / cap - 1)} | {len(closed)} | "
                     f"{'—' if not closed else f'{wins / len(closed):.0%}'} | {len(led.open_trades())} |")
    return lines


def write_main_report(rdir: Path, market: dict, shortlist: list[dict], ctx, dec, result: dict,
                      ledgers: dict, settings) -> Path:
    from trading.data.bitvavo import Bitvavo

    now_txt = madrid(market["generated_at"])
    lines = [f"# Informe principal — {now_txt} (Madrid)", "", _regime_line(market), "",
             f"**Modo real:** {'ACTIVO' if settings.mode.live_enabled else 'DESACTIVADO (solo simulación)'} · "
             f"capital real configurado {fmt_eur(settings.capital.live_eur)}", ""]
    proposals = [p for p in ledgers["live"].trades if p.trade_id in {x["trade_id"] for x in result["proposals"]}]
    lines += ["## Qué tienes que hacer", ""]
    if proposals:
        try:
            mk = Bitvavo().markets()
        except Exception:
            mk = None
        for t in proposals:
            dec_q = int(mk.loc[t.market, "quantityDecimals"]) if mk is not None and t.market in mk.index else 8
            lines += [order_block(t, dec_q), ""]
    else:
        reason = (result.get("live") or {}).get("blocked_reason")
        lines.append(f"No hay compras nuevas hoy{f' ({reason})' if reason else ''}.")
        lines.append("")
    for x in result.get("exit_actions", []):
        if x["portfolio"] == "live":
            lines.append(f"- **VENDER** `{x['trade_id']}` al mercado (≈ {fmt_price(x['price'])} €): {x['reason']}")
    lines += ["", "## Posiciones reales abiertas", ""]
    live_open = ledgers["live"].open_trades()
    if not live_open:
        lines.append("Ninguna.")
    else:
        lines += ["| ID | Moneda | Estado | Entrada | Stop | Objetivo | Salida máx. |", "|---|---|---|---|---|---|---|"]
        for t in live_open:
            lines.append(f"| `{t.trade_id}` | {t.base} | {t.status} | {fmt_price(t.entry_price or t.entry_limit)} | "
                         f"{fmt_price(t.stop_price)} | {fmt_price(t.tp_price)} | {madrid(t.time_exit_at)} |")
    lines += ["", "## Lista corta y decisión del agente trader", "",
              "| # | Moneda | Punt. | Decisión | Tamaño | Prob. | Motivo |", "|---|---|---|---|---|---|---|"]
    by_sym = {e.symbol: e for e in dec.entries} if dec else {}
    for s in shortlist:
        e = by_sym.get(s["symbol"])
        veto = result.get("hard_vetoes", {}).get(s["symbol"])
        if e:
            decision = "entrar" if e.action == "enter" else "descartar"
            lines.append(f"| {s['rank']} | {s['symbol']} | {s['score']:.3f} | {decision}{' (veto duro)' if veto else ''} | "
                         f"×{e.size_mult:g} | {e.p_tp_before_sl:.0%} | {e.rationale[:140]} |")
        else:
            lines.append(f"| {s['rank']} | {s['symbol']} | {s['score']:.3f} | {'veto duro' if veto else 'entrar (sin agente)'} | "
                         f"×1 | — | {veto or 'solo cuant'} |")
    rejected = (result.get("live") or {}).get("rejected") or []
    if rejected:
        lines += ["", "Descartes del motor de riesgo (real): " + "; ".join(f"{r['base']}: {r['reason']}" for r in rejected[:12])]
    if ctx:
        lines += ["", "## Contexto (agente investigador)", "", f"**Macro ({ctx.macro.regime}):** {ctx.macro.summary}", ""]
        for ev in ctx.events_next_10d[:8]:
            lines.append(f"- {ev.date}: {ev.event} (impacto {ev.impact})")
        flags = [(sym, rf) for sym, sc in ctx.symbols.items() for rf in sc.red_flags]
        if flags:
            lines += ["", "**Alertas:**"] + [f"- {sym}: {rf.type} ({rf.severity}) — {rf.text}" for sym, rf in flags[:12]]
        if ctx.channels:
            lines.append("")
            lines.append("Canales: " + ", ".join(f"{k}={v}" for k, v in ctx.channels.items()))
    if dec:
        lines += ["", f"**Visión del agente trader:** {dec.market_view}"]
    marks = {}
    try:
        marks = Bitvavo().tickers()["bid"].dropna().to_dict()
    except Exception:
        pass
    lines += ["", "## Carteras", ""] + portfolio_table(ledgers, settings, marks)
    v2c_path = rdir / "v2c.json"
    v2c = json.loads(v2c_path.read_text()) if v2c_path.exists() else {}
    lines += ["", "## V2C · tendencia BTC/ETH (simulada)", ""] + v2c_section(v2c, ledgers["V2C"])
    lines += ["", "## Notas técnicas", ""] + [f"- {n}" for n in result.get("notes", [])]
    lines.append(f"- Embudo del universo: {market.get('funnel')}; elegibles {market.get('n_eligible')}, "
                 f"candidatas {market.get('n_candidates')}.")
    lines.append("- Nada de esto es asesoramiento financiero. Las cifras simuladas no garantizan resultados reales.")
    text = "\n".join(lines) + "\n"
    (rdir / "report.md").write_text(text, encoding="utf-8")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"{datetime.fromisoformat(market['generated_at']).astimezone(MADRID):%Y-%m-%d_%H%M}_principal.md"
    out.write_text(text, encoding="utf-8")
    (rdir / "summary.txt").write_text(main_summary(market, proposals, result, v2c), encoding="utf-8")
    return out


def _usd(x) -> str:
    return "—" if x is None else f"{x:,.0f}".replace(",", ".")


def v2c_section(v2c: dict, ledger) -> list[str]:
    lines = ["Regla pre-registrada (variante v2c): se compra BTC/ETH si el cierre diario > SMA50 > SMA200. Se vende "
             "si el cierre diario cae por debajo de la SMA50, con stop a 3 ATR o a los 90 días. Es **solo "
             "simulación**, no una propuesta de compra.", ""]
    for sym, s in (v2c.get("signals") or {}).items():
        state = "en tendencia" if sym in (v2c.get("in_trend") or []) else "sin tendencia"
        lines.append(f"- {sym.removesuffix('USDT')}: cierre {_usd(s.get('close'))} $ · SMA50 {_usd(s.get('sma50'))} $ · "
                     f"SMA200 {_usd(s.get('sma200'))} $ → **{state}**")
    for t in v2c.get("entries") or []:
        lines.append(f"- Entrada simulada hoy: {t['base']} a {fmt_price(t['entry_price'])} € "
                     f"({fmt_eur(t['notional_eur'])}), stop {fmt_price(t['stop_price'])} €")
    for r in v2c.get("rejected") or []:
        if r["reason"] != "ya hay posición abierta":
            lines.append(f"- {r['base']} no entra: {r['reason']}")
    for t in v2c.get("exits") or []:
        lines.append(f"- Salida simulada: {t['base']} por {REASON_ES.get(t['exit_reason'], t['exit_reason'])} a "
                     f"{fmt_price(t['exit_price'])} € → {fmt_eur(t['pnl_eur'])}")
    opened = ledger.open_trades()
    if opened:
        lines.append("- Posiciones abiertas: " + "; ".join(
            f"{t.base} desde {madrid(t.opened_at)} a {fmt_price(t.entry_price)} €, stop {fmt_price(t.stop_price)} €, "
            f"salida máx. {madrid(t.time_exit_at)}" for t in opened))
    else:
        lines.append("- Sin posiciones abiertas.")
    if v2c.get("blocked_reason"):
        lines.append(f"- Entradas bloqueadas: {v2c['blocked_reason']}")
    return lines


def main_summary(market: dict, proposals: list, result: dict, v2c: dict | None = None) -> str:
    r = market["regime"]
    parts = [f"Régimen {REGIME_ES[r['label']]}."]
    if proposals:
        parts.append(f"{len(proposals)} compra(s) propuesta(s): " + "; ".join(
            f"{t.base} límite {fmt_price(t.entry_limit)} €, stop {fmt_price(t.stop_price)}, objetivo "
            f"{fmt_price(t.tp_price)} ({fmt_eur(t.notional_eur)})" for t in proposals) + ".")
    else:
        reason = (result.get("live") or {}).get("blocked_reason")
        parts.append("Sin compras nuevas" + (f" ({reason})." if reason else "."))
    for x in result.get("exit_actions", []):
        if x["portfolio"] == "live":
            parts.append(f"Vender {x['trade_id']}: {x['reason']}.")
    v2c = v2c or {}
    events = [f"compra {t['base']}" for t in v2c.get("entries") or []]
    events += [f"venta {t['base']} por {REASON_ES.get(t['exit_reason'], t['exit_reason'])} ({fmt_eur(t['pnl_eur'])})"
               for t in v2c.get("exits") or []]
    if events:
        parts.append("V2C (simulada): " + "; ".join(events) + ".")
    return " ".join(parts)


# --------------------------------------------------------------------------- vigilancia
def write_monitor_report(rdir: Path, data: dict, ledgers: dict, settings, marks: dict) -> Path:
    lines = [f"# Vigilancia — {madrid(data['generated_at'])} (Madrid)", "", "## Acciones", ""]
    actions = []
    for t in data["resolved_live"]:
        if t["status"] == "open":
            actions.append(f"- Tu compra `{t['trade_id']}` ({t['base']}) se da por **ejecutada** a {fmt_price(t['entry_price'])} €. "
                           f"Comprueba que tienes puesto el stop en {fmt_price(t['stop_price'])} €. Si no la ejecutaste, "
                           f"márcala `no_ejecutada` en `ledger/live/fills.csv`.")
        elif t["status"] == "cancelled":
            actions.append(f"- La orden `{t['trade_id']}` ({t['base']}) **caducó**: cancélala en Bitvavo si sigue abierta.")
    for t in data["closed"].get("live", []):
        actions.append(f"- `{t['trade_id']}` ({t['base']}): {REASON_ES.get(t['exit_reason'], t['exit_reason'])} a "
                       f"{fmt_price(t['exit_price'])} € → P&L {fmt_eur(t['pnl_eur'])} ({t['r_multiple']:+.2f} R). "
                       f"Comprueba en Bitvavo que se ejecutó y cancela la orden contraria.")
    for s in data["time_exits_soon"]:
        actions.append(f"- **Vende {s['base']}** (`{s['trade_id']}`): salida por tiempo {madrid(s['time_exit_at'])}. "
                       f"Bid actual {fmt_price(s['bid']) if s['bid'] else '—'} €.")
    for a in data["news_alerts"]:
        heads = "; ".join(h["title"] for h in a["headlines"][:3])
        actions.append(f"- Noticias sobre **{a['base']}** (`{a['trade_id']}`): {heads}")
    lines += actions or ["Nada que hacer."]
    lines += ["", "## Carteras", ""] + portfolio_table(ledgers, settings, marks)
    text = "\n".join(lines) + "\n"
    (rdir / "report.md").write_text(text, encoding="utf-8")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"{datetime.fromisoformat(data['generated_at']).astimezone(MADRID):%Y-%m-%d_%H%M}_vigilancia.md"
    out.write_text(text, encoding="utf-8")
    summary = " ".join(a.lstrip("- ").replace("**", "").replace("`", "") for a in actions) or "Vigilancia: nada que hacer."
    (rdir / "summary.txt").write_text(summary, encoding="utf-8")
    return out
