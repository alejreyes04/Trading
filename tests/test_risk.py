from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from trading.ledger import Ledger, Trade
from trading.orders import MarketRules
from trading.risk import Candidate, plan_entries

RULES = MarketRules(tick_size=0.0001, quantity_decimals=4, min_quote=5.0, min_base=0.0)
NOW = datetime(2026, 9, 28, 6, 0, tzinfo=timezone.utc)


def cand(base, ask=10.0, atr_pct=0.05, size=1.0):
    return Candidate(base=base, market=f"{base}-EUR", binance_symbol=f"{base}USDT", score=0.7, ask_eur=ask,
                     atr_pct=atr_pct, slippage=0.001, rules=RULES, size_mult=size)


def test_risk_per_trade_and_heat_limit(tmp_path, settings):
    led = Ledger("Q", root=tmp_path)
    plan = plan_entries([cand(c) for c in "ABCDEF"], led, 500, {}, 1.0, settings, None, NOW)
    # 1 % de 500 = 5 € por operación; el límite de riesgo total (4 %) permite 4 posiciones
    assert len(plan.entries) == 4
    for e in plan.entries:
        assert abs(e.risk_eur - 5.0) < 0.05
        assert e.stop_price < e.entry_limit < e.tp_price
        assert e.notional_eur <= 0.25 * 500 + 1e-6
    assert any("riesgo total" in r for _, r in plan.rejected)


def test_regime_off_and_veto(tmp_path, settings):
    led = Ledger("Q", root=tmp_path)
    assert plan_entries([cand("A")], led, 500, {}, 0.0, settings, None, NOW).blocked_reason
    plan = plan_entries([cand("A", size=0.0), cand("B", size=0.5)], led, 500, {}, 1.0, settings, None, NOW)
    assert [e.candidate.base for e in plan.entries] == ["B"]
    assert abs(plan.entries[0].risk_eur - 2.5) < 0.05          # tamaño ×0,5


def test_llm_cannot_increase_size(tmp_path, settings):
    led = Ledger("Q", root=tmp_path)
    plan = plan_entries([cand("A", size=3.0)], led, 500, {}, 1.0, settings, None, NOW)
    assert abs(plan.entries[0].risk_eur - 5.0) < 0.05


def test_position_cap_with_tight_stop(tmp_path, settings):
    led = Ledger("Q", root=tmp_path)
    plan = plan_entries([cand("A", atr_pct=0.005)], led, 500, {}, 1.0, settings, None, NOW)
    assert plan.entries[0].notional_eur <= 125.0 + 1e-6      # 25 % del capital


def test_drawdown_pause_and_cooldown(tmp_path, settings):
    led = Ledger("Q", root=tmp_path)
    led.dir.mkdir(parents=True)
    pd.DataFrame([{"time": "x", "run_id": "r", "equity": 1000, "cash": 0, "open_risk": 0, "n_open": 0,
                   "n_pending": 0}]).to_csv(led.equity_path, index=False)
    assert "caída" in plan_entries([cand("A")], led, 500, {}, 1.0, settings, None, NOW).blocked_reason
    led2 = Ledger("QL", root=tmp_path)
    t = Trade("QL-x-A", "QL", "A", "A-EUR", "AUSDT", "closed", "r", NOW.isoformat(), NOW.isoformat(), 10, 10, 1, 10,
              9, 8.9, 12, NOW.isoformat(), None, 1, closed_at=(NOW - timedelta(days=1)).isoformat(), pnl_eur=0.0)
    led2.trades.append(t)
    plan = plan_entries([cand("A")], led2, 500, {}, 1.0, settings, None, NOW)
    assert not plan.entries and "espera" in plan.rejected[0][1]


def test_correlation_filter(tmp_path, settings):
    led = Ledger("Q", root=tmp_path)
    rng = np.random.default_rng(0)
    base = rng.normal(0, 0.03, 90)
    rets = pd.DataFrame({"AUSDT": base, "BUSDT": base + rng.normal(0, 0.001, 90), "CUSDT": rng.normal(0, 0.03, 90)})
    plan = plan_entries([cand("A"), cand("B"), cand("C")], led, 500, {}, 1.0, settings, rets, NOW)
    assert [e.candidate.base for e in plan.entries] == ["A", "C"]


def test_min_order_rejected(tmp_path, settings):
    led = Ledger("Q", root=tmp_path)
    rules = MarketRules(tick_size=0.0001, quantity_decimals=4, min_quote=500.0, min_base=0.0)
    c = cand("A")
    c.rules = rules
    plan = plan_entries([c], led, 500, {}, 1.0, settings, None, NOW)
    assert not plan.entries and "mínimo" in plan.rejected[0][1]


def test_drawdown_pause_is_temporary(tmp_path, settings):
    led = Ledger("Q", root=tmp_path)
    led.dir.mkdir(parents=True)
    pd.DataFrame([{"time": "2026-09-01T00:00:00+00:00", "run_id": "r", "equity": 1000, "cash": 0, "open_risk": 0,
                   "n_open": 0, "n_pending": 0}]).to_csv(led.equity_path, index=False)
    first = plan_entries([cand("A")], led, 500, {}, 1.0, settings, None, NOW)
    assert "pausa" in first.blocked_reason
    during = plan_entries([cand("A")], led, 500, {}, 1.0, settings, None, NOW + timedelta(days=5))
    assert "hasta" in during.blocked_reason
    after = plan_entries([cand("A")], led, 500, {}, 1.0, settings, None,
                         NOW + timedelta(days=settings.risk.drawdown_pause_days + 1))
    assert after.blocked_reason is None and after.entries      # el máximo se reinicia y se vuelve a operar
    led.save()
    assert Ledger("Q", root=tmp_path).state["peak_baseline"] == 500
