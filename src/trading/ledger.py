"""Registro de operaciones por cartera (CSV legible) y cálculo de capital.

Carteras:
  Q    simulada, solo parte cuantitativa
  QL   simulada, cuant + decisiones del agente trader (LLM)
  RND  simulada, entradas aleatorias con las mismas reglas de salida (referencia nula)
  V2C  simulada, tendencia en BTC/ETH durante semanas (variante v2c de research/hypotheses.md)
  live dinero real: propuestas que ejecutas tú (con tus correcciones en ledger/live/fills.csv)
"""

from __future__ import annotations

import json
from dataclasses import MISSING, asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from trading.paths import LEDGER_DIR

PORTFOLIOS = ("live", "Q", "QL", "RND", "V2C")
PORTFOLIO_NAMES = {"live": "Real (tú)", "Q": "Q · solo cuant", "QL": "QL · cuant + agente",
                   "RND": "RND · aleatoria", "V2C": "V2C · tendencia BTC/ETH"}
OPEN_STATES = ("open", "pending")


@dataclass
class Trade:
    trade_id: str
    portfolio: str
    base: str
    market: str
    binance_symbol: str
    status: str                  # pending | open | closed | cancelled
    run_id: str
    proposed_at: str
    opened_at: str | None
    entry_limit: float
    entry_price: float | None
    qty: float
    notional_eur: float
    stop_price: float
    stop_limit_price: float
    tp_price: float
    time_exit_at: str
    expires_at: str | None
    risk_eur: float
    score: float | None = None
    size_mult: float = 1.0
    llm_prob: float | None = None
    entry_fee_eur: float = 0.0
    closed_at: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    exit_fee_eur: float = 0.0
    pnl_eur: float | None = None
    r_multiple: float | None = None
    last_checked_at: str | None = None
    notes: str = ""

    def close(self, when: str, price: float, reason: str, fee_rate: float) -> None:
        self.status = "closed"
        self.closed_at = when
        self.exit_price = price
        self.exit_reason = reason
        self.exit_fee_eur = round(price * self.qty * fee_rate, 6)
        gross = (price - (self.entry_price or 0)) * self.qty
        self.pnl_eur = round(gross - self.entry_fee_eur - self.exit_fee_eur, 6)
        self.r_multiple = round(self.pnl_eur / self.risk_eur, 4) if self.risk_eur else None


FIELDS = [f.name for f in fields(Trade)]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Ledger:
    def __init__(self, portfolio: str, root: Path = LEDGER_DIR):
        self.portfolio = portfolio
        self.dir = root / portfolio
        self.trades_path = self.dir / "trades.csv"
        self.equity_path = self.dir / "equity.csv"
        self.state_path = self.dir / "state.json"
        self.trades: list[Trade] = self._load()
        self.state: dict = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    def _load(self) -> list[Trade]:
        if not self.trades_path.exists():
            return []
        df = pd.read_csv(self.trades_path, dtype=str, keep_default_na=False)
        out = []
        for rec in df.to_dict("records"):
            kwargs = {}
            for f in fields(Trade):
                val = rec.get(f.name, "")
                if val == "":
                    kwargs[f.name] = f.default if f.default is not MISSING else None
                else:
                    kwargs[f.name] = _coerce(f.type, val)
            out.append(Trade(**kwargs))
        return out

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame([asdict(t) for t in self.trades], columns=FIELDS)
        df.to_csv(self.trades_path, index=False)
        if self.state:
            self.state_path.write_text(json.dumps(self.state, indent=2))

    # --- consultas -----------------------------------------------------------
    def open_trades(self) -> list[Trade]:
        return [t for t in self.trades if t.status in OPEN_STATES]

    def closed_trades(self) -> list[Trade]:
        return [t for t in self.trades if t.status == "closed"]

    def realized_pnl(self) -> float:
        return sum(t.pnl_eur or 0.0 for t in self.closed_trades())

    def equity(self, capital: float, marks: dict[str, float]) -> float:
        """Capital + P&L realizado + P&L latente de posiciones abiertas (valoradas al bid)."""
        unreal = 0.0
        for t in self.trades:
            if t.status == "open" and t.entry_price is not None:
                mark = marks.get(t.market, t.entry_price)
                unreal += (mark - t.entry_price) * t.qty - t.entry_fee_eur
        return capital + self.realized_pnl() + unreal

    def cash(self, capital: float) -> float:
        """Efectivo disponible: capital + realizado − coste de posiciones abiertas y órdenes pendientes."""
        committed = sum((t.entry_price or t.entry_limit) * t.qty + t.entry_fee_eur for t in self.open_trades())
        return capital + self.realized_pnl() - committed

    def open_risk(self) -> float:
        return sum(t.risk_eur for t in self.open_trades())

    def peak_equity(self, capital: float) -> float:
        """Máximo de capital desde el último reinicio (tras una pausa por drawdown)."""
        baseline = float(self.state.get("peak_baseline", capital))
        if not self.equity_path.exists():
            return baseline
        df = pd.read_csv(self.equity_path)
        if self.state.get("peak_reset_at"):
            df = df[df["time"] >= self.state["peak_reset_at"]]
        return float(max(baseline, df["equity"].max())) if not df.empty else baseline

    # --- pausa por drawdown (interfaz común con el Book del backtest) ---
    def pause_until(self) -> datetime | None:
        v = self.state.get("pause_until")
        return datetime.fromisoformat(v) if v else None

    def start_pause(self, until: datetime) -> None:
        self.state["pause_until"] = until.isoformat()

    def reset_peak(self, equity: float, now: datetime) -> None:
        self.state.update({"pause_until": None, "peak_reset_at": now.isoformat(), "peak_baseline": equity})

    def last_closed_at(self, base: str) -> str | None:
        closed = [t.closed_at for t in self.closed_trades() if t.base == base and t.closed_at]
        return max(closed) if closed else None

    def record_equity(self, run_id: str, capital: float, marks: dict[str, float]) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        row = {"time": now_iso(), "run_id": run_id, "equity": round(self.equity(capital, marks), 4),
               "cash": round(self.cash(capital), 4), "open_risk": round(self.open_risk(), 4),
               "n_open": len([t for t in self.trades if t.status == "open"]),
               "n_pending": len([t for t in self.trades if t.status == "pending"])}
        header = not self.equity_path.exists()
        pd.DataFrame([row]).to_csv(self.equity_path, mode="a", header=header, index=False)
        return row


def _coerce(type_hint, val: str):
    th = str(type_hint)
    if "float" in th:
        return float(val)
    if "int" in th:
        return int(float(val))
    return val


def load_meta(root: Path = LEDGER_DIR) -> dict:
    path = root / "meta.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save_meta(meta: dict, root: Path = LEDGER_DIR) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))


FILLS_COLUMNS = ["trade_id", "estado", "precio_entrada", "cantidad", "precio_salida", "fecha_salida", "notas"]


def load_fills(root: Path = LEDGER_DIR) -> pd.DataFrame:
    """Correcciones manuales de la cartera real (las editas tú en ledger/live/fills.csv).

    estado: ejecutada | no_ejecutada | cerrada
    """
    path = root / "live" / "fills.csv"
    if not path.exists():
        return pd.DataFrame(columns=FILLS_COLUMNS)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def ensure_fills_template(root: Path = LEDGER_DIR) -> None:
    path = root / "live" / "fills.csv"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(columns=FILLS_COLUMNS).to_csv(path, index=False)
