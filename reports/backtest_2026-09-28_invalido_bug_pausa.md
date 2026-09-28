> **INVÁLIDO:** este backtest tenía un error (la pausa por drawdown bloqueaba las entradas para siempre desde 2023). Ver `backtest_2026-09-28.md` para el resultado correcto.

# Backtest de la parte cuantitativa — 2026-09-28

Periodo 2021-01-01 → 2026-09-28 (2096 días) · 644 pares USDT de Binance vistos (incluye retirados) · capital inicial 500.0 · reglas fijadas a priori (1 variante probada).

Costes: comisión taker Bitvavo 0,25 % (maker 0,15 % en objetivos) + slippage 0,05–0,20 % según liquidez + 0,5 % extra en stops.

## Resultado global

| Serie | Rentab. total | CAGR | Volatilidad | Sharpe | Sortino | Caída máx. |
|---|---|---|---|---|---|---|
| **Q (estrategia)** | +3.1 % | +0.5 % | +7.9 % | 0.11 | 0.08 | -15.7 % |
| BTC comprar y mantener | +188.0 % | +20.3 % | +57.2 % | 0.61 | 0.87 | -76.6 % |
| Cesta top-20 igual ponderada | -37.8 % | -7.9 % | +82.0 % | 0.32 | 0.43 | -92.9 % |
| RND (mediana de 100 carteras aleatorias) | +17.0 % | — | — | 0.37 | — | — |

## Operaciones

- Operaciones: 309 · aciertos +43.0 % · R medio -0.002 (IC 95 % -0.112 a +0.108) · profit factor 1.03
- Duración media 7.4 días · tiempo invertido +25.6 % de los días · comisiones pagadas +5.4 % del capital inicial
- Salidas: {'tiempo': 143, 'stop': 92, 'objetivo': 73, 'retirada': 1}
- Probabilidad de que el Sharpe real sea > 0 (PSR): 60.1%
- Frente a 100 carteras aleatorias con las mismas reglas: la estrategia supera al 4% en rentabilidad, al 4% en Sharpe y al 2% en R medio (rango aleatorio p5–p95 de rentabilidad: +3.3 % a +36.9 %).

## Por año

| Año | Q (estrategia) | BTC comprar y mantener | Cesta top-20 igual ponderada | RND (mediana) |
|---|---|---|---|---|
| 2021 | +14.4 % | +57.6 % | +240.5 % | +26.1 % |
| 2022 | -5.0 % | -65.3 % | -84.4 % | -2.3 % |
| 2023 | -6.0 % | +154.5 % | +95.7 % | -5.4 % |
| 2024 | +0.0 % | +111.8 % | +10.8 % | +0.0 % |
| 2025 | +0.0 % | -7.3 % | -57.3 % | +0.0 % |
| 2026 | +0.0 % | -4.9 % | +6.2 % | +0.0 % |

## Por régimen de mercado (al abrir la operación)

Días en cada régimen: risk_off 40%, neutral 30%, risk_on 30%

| Régimen | Operaciones | R medio | Aciertos |
|---|---|---|---|
| neutral | 175 | -0.061 | 41% |
| risk_on | 134 | +0.075 | 46% |

## Cómo leerlo

- Si el intervalo de confianza del R medio incluye 0, **no hay evidencia estadística de ventaja**.
- Superar a las carteras aleatorias indica que la *selección* aporta algo más allá de las reglas de salida y del régimen; no superarlas indica que lo que funcione se debe a estar invertido en mercado alcista.
- El backtest no incluye al agente LLM (no se puede testear sin sesgo de anticipación).
- Tiempo de cálculo: 726.4 s.
