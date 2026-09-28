# Hipótesis pre-registradas — investigación de variantes (28-09-2026)

Este documento se escribe y se sube al repositorio **antes** de ejecutar ninguna de las variantes. Fija las
reglas, los parámetros y los criterios de éxito para que no se puedan ajustar después de ver los resultados.

## Punto de partida

- La estrategia v1 no mostró ventaja en el backtest de 2021 a 2026 (`reports/backtest_2026-09-28.md`):
  - R medio −0,036, con un intervalo de confianza al 95 % de [−0,103, +0,033].
  - Percentil 19 frente a carteras aleatorias con las mismas reglas.
- Variantes probadas hasta ahora: 1 (v1). Con estas tres, el total es **N = 4**, que es el número usado para
  corregir el Sharpe por pruebas múltiples (Sharpe deflactado).

## Reglas comunes (idénticas a la v1 salvo donde se indica)

- **Datos:** velas diarias de Binance en USDT, pares retirados incluidos, periodo 2021-01-01 → 2026-09-28.
- **Ejecución:** señal al cierre del día; entrada a la apertura del día siguiente con slippage según
  liquidez y comisión taker de Bitvavo.
- **Riesgo:** 1 % del capital por operación, riesgo total abierto ≤ 4 %, máximo 5 posiciones, máximo 25 % del
  capital por moneda, filtro de correlación 0,85, pausa de 14 días tras una caída del 15 %.
- **Elegibilidad:** al menos 90 días de historial y volumen diario mediano de 30 días ≥ 3 M$.
- **Lista corta:** 12 candidatas por día.
- **Salidas por defecto:** stop a 2 ATR, objetivo a 3 ATR, salida a los 10 días.

## Variantes

### v2a · solo risk-on
- **Hipótesis:** la v1 solo aporta algo cuando el mercado es claramente alcista.
- **Aviso:** es **post-hoc**, porque la idea sale del desglose por régimen de la v1. Por eso su resultado
  cuenta menos como evidencia.
- **Cambio:** solo se abren entradas nuevas si el régimen es `risk_on`. Todo lo demás, igual que la v1.

### v2b · retroceso en tendencia (2–10 días)
- **Hipótesis:** en cripto, a corto plazo lo que más ha subido tiende a revertir. Es mejor comprar caídas
  moderadas dentro de una tendencia alcista que perseguir lo más estirado.
- **Candidata si cumple todo:**
  - cierre > EMA50;
  - EMA20 > EMA50;
  - cierre ≤ EMA20 − 0,5 × ATR14;
  - RSI14 ≥ 30;
  - régimen distinto de risk-off.
- **Orden:** momentum ajustado de 28 días, de mayor a menor. Se toman las 12 primeras.
- **Salidas:** las de la v1.

### v2c · tendencia en BTC/ETH (semanas)
- **Hipótesis:** el momentum temporal funciona mejor en los activos grandes y a horizontes de semanas
  (Liu & Tsyvinski 2021).
- **Aviso:** **se sale del horizonte de 2–10 días** que pedía el usuario.
- **Universo:** BTCUSDT y ETHUSDT.
- **Entrada:** cierre > SMA50 y SMA50 > SMA200. No depende del régimen general: la señal es la propia tendencia.
- **Salidas:**
  - el cierre queda por debajo de la SMA50: se sale al cierre de ese día con slippage;
  - stop a 3 ATR;
  - salida a los 90 días;
  - **sin objetivo**.
- **Riesgo:** el mismo motor, con el stop a 3 ATR para calcular el tamaño.

## Criterios de éxito (hay que cumplirlos todos)

1. En el periodo completo, R medio > 0 y **Sharpe deflactado ≥ 0,90** con N = 4.
2. **Rentabilidad positiva en ambas mitades**: 2021–2023 y 2024–2026.
3. **Caída máxima ≤ 25 %.**
4. Comparación con el azar:
   - **v2a y v2b:** percentil ≥ 80 en Sharpe frente a 50 carteras aleatorias con sus mismas reglas
     (mismo universo elegible y régimen, 12 candidatas al azar).
   - **v2c:** Sharpe superior al de comprar y mantener BTC en el mismo periodo.

## Qué se hace con el resultado

- **Si una variante cumple los criterios:** se propone añadirla como cartera **simulada** al sistema diario.
  El dinero real sigue desactivado hasta que cumpla los criterios hacia delante del README y el usuario lo
  decida.
- **Si ninguna los cumple:** se comunica tal cual y no se prueban variantes nuevas sin pre-registrarlas.
