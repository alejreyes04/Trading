from datetime import datetime, timezone

import pandas as pd

from trading.ledger import Ledger, Trade
from trading.paper import check_exit, resolve_pending, update_exits


def trade(**kw):
    base = dict(trade_id="live-r-A", portfolio="live", base="A", market="A-EUR", binance_symbol="AUSDT",
                status="open", run_id="r", proposed_at="2026-09-01T06:00:00+00:00",
                opened_at="2026-09-01T06:00:00+00:00", entry_limit=10.0, entry_price=10.0, qty=10.0,
                notional_eur=100.0, stop_price=9.0, stop_limit_price=8.9, tp_price=11.5,
                time_exit_at="2026-09-11T06:00:00+00:00", expires_at="2026-09-01T12:00:00+00:00", risk_eur=10.0,
                entry_fee_eur=0.25)
    base.update(kw)
    return Trade(**base)


def candles(rows, start="2026-09-01T07:00:00Z"):
    idx = pd.date_range(start, periods=len(rows), freq="h", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1.0)


def test_stop_before_target_same_candle(settings):
    res = check_exit(trade(), candles([[10, 12, 8.5, 10]]), settings)
    assert res[2] == "stop" and res[1] < 9.0


def test_gap_below_stop_exits_at_open(settings):
    res = check_exit(trade(), candles([[8.0, 8.2, 7.9, 8.1]]), settings)
    assert res[2] == "stop" and res[1] < 8.0


def test_target_and_time_exit(settings):
    assert check_exit(trade(), candles([[10, 10.5, 9.8, 10.2], [10.2, 11.6, 10.1, 11.4]]), settings)[2] == "objetivo"
    late = candles([[10, 10.2, 9.9, 10]], start="2026-09-11T07:00:00Z")
    assert check_exit(trade(), late, settings)[2] == "tiempo"


def test_close_pnl_includes_fees(settings):
    t = trade()
    t.close("2026-09-02T00:00:00+00:00", 11.5, "objetivo", settings.exchange.maker_fee)
    expected = (11.5 - 10.0) * 10 - 0.25 - 11.5 * 10 * settings.exchange.maker_fee
    assert abs(t.pnl_eur - expected) < 1e-6
    assert abs(t.r_multiple - expected / 10.0) < 1e-4


def test_update_exits_and_pending_resolution(tmp_path, settings):
    led = Ledger("live", root=tmp_path)
    led.trades = [trade(), trade(trade_id="live-r-B", base="B", market="B-EUR", status="pending", opened_at=None,
                                 entry_price=None, entry_limit=5.0, stop_price=4.5, tp_price=6.0)]
    data = {"A-EUR": candles([[10, 10.4, 9.95, 10.1], [10.1, 11.6, 10.0, 11.5]]),
            "B-EUR": candles([[5.2, 5.3, 4.95, 5.1]])}
    fetch = lambda m, s: data[m]
    now = datetime(2026, 9, 1, 13, tzinfo=timezone.utc)
    resolved = resolve_pending(led, fetch, settings, now)
    assert resolved[0].status == "open" and resolved[0].entry_price == 5.0
    closed = update_exits(led, fetch, settings, now)
    assert [t.base for t in closed] == ["A"] and closed[0].exit_reason == "objetivo"


def test_manual_fills_override(tmp_path, settings):
    led = Ledger("live", root=tmp_path)
    led.trades = [trade(status="pending", opened_at=None, entry_price=None)]
    (tmp_path / "live").mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"trade_id": "live-r-A", "estado": "no_ejecutada", "precio_entrada": "", "cantidad": "",
                   "precio_salida": "", "fecha_salida": "", "notas": ""}]).to_csv(tmp_path / "live" / "fills.csv", index=False)
    resolve_pending(led, lambda m, s: candles([[10, 10, 9.9, 10]]), settings, datetime(2026, 9, 1, 13, tzinfo=timezone.utc))
    assert led.trades[0].status == "cancelled"


def test_ledger_roundtrip(tmp_path):
    led = Ledger("Q", root=tmp_path)
    led.trades = [trade(llm_prob=0.55)]
    led.save()
    again = Ledger("Q", root=tmp_path).trades[0]
    assert again.entry_price == 10.0 and again.llm_prob == 0.55 and again.closed_at is None and again.notes == ""
