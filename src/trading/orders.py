"""Redondeos según las reglas de cada mercado de Bitvavo y texto de las órdenes para copiar en la app."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal


@dataclass
class MarketRules:
    tick_size: float
    quantity_decimals: int
    min_quote: float
    min_base: float

    @classmethod
    def from_row(cls, row) -> "MarketRules":
        return cls(float(row["tickSize"]), int(row["quantityDecimals"]),
                   float(row["minOrderInQuoteAsset"]), float(row["minOrderInBaseAsset"]))


def round_price(price: float, tick: float, up: bool) -> float:
    """Ajusta el precio a un múltiplo del tick (hacia arriba o hacia abajo)."""
    if tick <= 0 or not math.isfinite(price):
        return price
    q = Decimal(str(price)) / Decimal(str(tick))
    q = q.to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return float(q * Decimal(str(tick)))


def round_qty(qty: float, decimals: int) -> float:
    """Redondea la cantidad hacia abajo (nunca comprar más de lo planeado)."""
    step = Decimal(1).scaleb(-decimals)
    return float(Decimal(str(qty)).quantize(step, rounding=ROUND_FLOOR))


def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if p >= 1:
        return f"{p:.4f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{p:.10f}".rstrip("0").rstrip(".").replace(".", ",")


def fmt_eur(x: float) -> str:
    return f"{x:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_qty(q: float, decimals: int) -> str:
    return f"{q:.{decimals}f}".replace(".", ",") if decimals > 0 else f"{int(q)}"
