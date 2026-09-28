"""Esquemas de los ficheros que escriben los agentes (context.json y decisions.json).

Validarlos en código garantiza que el agente LLM no puede colar campos ni ampliar el riesgo.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Strict):
    text: str
    source: str | None = None       # URL
    date: str | None = None         # AAAA-MM-DD


class RedFlag(Evidence):
    type: Literal["hack", "delisting", "regulacion", "unlock", "litigio", "depeg", "equipo", "otro"]
    severity: Literal["alta", "media", "baja"] = "media"


class Social(Strict):
    mentions_24h: int | None = None
    mentions_baseline_7d: float | None = None
    positive_share: float | None = Field(default=None, ge=0, le=1)
    suspected_bot_share: float | None = Field(default=None, ge=0, le=1)
    notable_accounts: list[str] = []
    sample_urls: list[str] = []


class SymbolContext(Strict):
    red_flags: list[RedFlag] = []
    catalysts: list[Evidence] = []
    sentiment: int | None = Field(default=None, ge=-2, le=2)
    social: Social | None = None
    summary: str = ""


class Event(Strict):
    date: str
    event: str
    impact: Literal["alto", "medio", "bajo"]
    source: str | None = None


class Macro(Strict):
    regime: Literal["risk_on", "neutral", "risk_off", "desconocido"]
    summary: str
    drivers: list[Evidence] = []


class Context(Strict):
    run_id: str
    generated_at: str
    macro: Macro
    events_next_10d: list[Event] = []
    channels: dict[str, str] = {}
    symbols: dict[str, SymbolContext] = {}


class EntryDecision(Strict):
    symbol: str
    action: Literal["enter", "skip"]
    size_mult: float
    p_tp_before_sl: float = Field(ge=0, le=1)
    rationale: str
    key_risks: list[str] = []
    evidence_refs: list[str] = []

    @field_validator("size_mult")
    @classmethod
    def _allowed(cls, v: float) -> float:
        if v not in (0, 0.5, 1):
            raise ValueError("size_mult solo puede ser 0, 0.5 o 1 (el agente no puede ampliar riesgo)")
        return float(v)

    @model_validator(mode="after")
    def _coherent(self) -> "EntryDecision":
        if self.action == "skip" and self.size_mult != 0:
            raise ValueError("si action=skip, size_mult debe ser 0")
        if self.action == "enter" and self.size_mult == 0:
            raise ValueError("si action=enter, size_mult debe ser 0.5 o 1")
        return self


class ExitDecision(Strict):
    trade_id: str
    action: Literal["hold", "close"]
    reason: str


class Decisions(Strict):
    run_id: str
    generated_at: str
    market_view: str
    entries: list[EntryDecision]
    exits: list[ExitDecision] = []


def check_decisions_against(decisions: Decisions, shortlist_symbols: list[str], open_trade_ids: list[str]) -> list[str]:
    """Errores de coherencia con la lista corta y las posiciones abiertas (lista vacía = OK)."""
    errors = []
    seen = [e.symbol for e in decisions.entries]
    for s in seen:
        if s not in shortlist_symbols:
            errors.append(f"{s} no está en la lista corta: el agente solo puede elegir dentro de ella")
    for s in shortlist_symbols:
        if s not in seen:
            errors.append(f"falta decisión para {s}")
    if len(set(seen)) != len(seen):
        errors.append("hay símbolos repetidos en entries")
    for e in decisions.exits:
        if e.trade_id not in open_trade_ids:
            errors.append(f"trade_id {e.trade_id} no es una posición abierta")
    return errors
