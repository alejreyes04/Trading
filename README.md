# Trading — sistema de análisis cripto con agentes Claude

Sistema semiautomático: **analiza, propone y lleva la cuenta; tú ejecutas**.

- Tres veces al día analiza las criptomonedas que se pueden comprar en **Bitvavo** (exchange con licencia MiCA).
- Cruza ese análisis con noticias, macro y sentimiento.
- Propone operaciones de 2 a 10 días con órdenes listas para copiar.
- Registra todo para medir con rigor si funciona.

> **Aviso honesto.** Nada de esto garantiza beneficios. En las altcoins, un movimiento típico en 5 días es de
> ±12 %, así que cada operación individual es casi una moneda al aire. La «seguridad» solo existe a nivel de
> cartera: tamaño pequeño por operación, límites duros y muchas operaciones medidas. Esto no es asesoramiento
> financiero.

---

## Resultado del backtest y estado actual: SOLO SIMULACIÓN

Antes de arriesgar dinero probé la parte cuantitativa con 5,7 años de histórico diario de Binance
(2021 → sep-2026, 644 pares USDT, **incluidos los ya retirados**). Usé las mismas reglas, los costes de
Bitvavo y el mismo motor de riesgo que el sistema real. Informe completo: `reports/backtest_2026-09-28.md`.

| Serie | Rentabilidad total | Sharpe | Caída máxima |
|---|---|---|---|
| **Estrategia v1 (Q)** | **−14,2 %** | −0,16 | −39,7 % |
| Carteras aleatorias con las mismas reglas (mediana de 100) | −2,6 % | 0,03 | — |
| BTC comprar y mantener | +188,0 % | 0,61 | −76,6 % |
| Cesta top-20 de altcoins igual ponderada | −37,8 % | 0,32 | −92,9 % |

- **808 operaciones:**
  - Aciertos: 40,6 %, justo lo que daría el azar con stop a 2 ATR y objetivo a 3 ATR.
  - R medio: −0,036, con un intervalo de confianza al 95 % entre −0,10 y +0,03, es decir, **sin
    evidencia de ventaja**.
  - Comisiones: se llevaron el 13,9 % del capital inicial.
- **La selección no aporta valor:** elegir monedas al azar con las mismas reglas habría dado un resultado
  igual o mejor. La estrategia queda en el percentil 19.
- **Conclusión honesta:** comprar las altcoins con más momentum para venderlas en 2–10 días **no funcionó**
  en este periodo, ni siquiera con riesgo controlado. Por eso `mode.live_enabled` está en `false`.
- **Qué sigue funcionando:** el sistema opera en **simulación** (Q, QL, RND y V2C) para acumular datos reales
  hacia delante y medir si el agente de IA aporta algo. Puedes activar el modo real editando
  `config/settings.yaml`, pero sería en contra de esta evidencia.
- Registro de variantes probadas en `reports/experiments.csv`. La primera ejecución está marcada como
  inválida por un error de programación que ya está corregido.

### Investigación de variantes (pre-registrada)

Después del resultado negativo probé tres variantes. Sus reglas y los criterios de éxito se fijaron y se
subieron al repo **antes** de ejecutarlas (`research/hypotheses.md`), para no ajustar nada a posteriori.
Informe completo: `reports/research_2026-09-28.md`.

| Variante | Rentabilidad | Sharpe | Caída máx. | Operaciones | R medio (IC 95 %) | Sharpe deflactado | ¿Cumple? |
|---|---|---|---|---|---|---|---|
| v2a · solo en mercado alcista | +1,1 % | 0,07 | −31,5 % | 393 | +0,01 (−0,09 a +0,11) | 12 % | ❌ No mejora al azar (percentil 18) |
| v2b · comprar retrocesos dentro de una tendencia | −20,5 % | −0,52 | −32,4 % | 394 | **−0,10 (−0,17 a −0,03)** | 0 % | ❌ Pierde de forma clara |
| v2c · tendencia en BTC/ETH (semanas, no días) | **+28,6 %** | **0,74** | **−6,7 %** | 56 | **+0,46 (+0,05 a +0,93)** | 67 % | ❌ Solo falla el Sharpe deflactado ≥ 90 % |
| *Referencia: BTC comprar y mantener* | +188,0 % | 0,61 | −76,6 % | — | — | — | — |

- **Ninguna variante cumple todos los criterios**, así que el modo real sigue desactivado.
- **v2c es la única prometedora:**
  - Gana en las dos mitades del periodo (+18,3 % en 2021–23 y +8,7 % en 2024–26).
  - Su Sharpe es mejor que el de mantener BTC, con una caída máxima de −6,7 % frente a −76,6 %.
  - Pero tiene solo 56 operaciones, así que la evidencia estadística todavía es insuficiente. Además está
    fuera de tu horizonte de 2–10 días: mantiene posiciones durante semanas.
  - Su rentabilidad es mucho menor que la de mantener BTC, porque solo está invertida el 38 % del tiempo y
    con posiciones pequeñas.
- **Qué hacemos con ella:** se sigue **hacia delante en simulación** como cartera **V2C**, con las mismas
  reglas. Si con el tiempo acumula evidencia, será la candidata a dinero real, siempre por decisión tuya.

---

## Cómo funciona (cada día, sin que tengas que programar nada)

| Hora (Madrid) | Qué ocurre | Te llega |
|---|---|---|
| ~07:47 | **Ejecución principal:** escáner cuant → agente investigador → agente trader → motor de riesgo | Notificación con las compras propuestas (simuladas mientras el modo real esté desactivado) + informe en `reports/` |
| ~13:47 y ~20:47 | **Vigilancia:** stops u objetivos alcanzados, órdenes caducadas, salidas por tiempo, noticias sobre tus posiciones | Notificación solo si hay algo que hacer |

Por dentro:

1. **Escáner cuant (Python):**
   - **Universo:** las 496 monedas de Binance con par USDT cruzadas con los ~428 pares en EUR de Bitvavo. Tras
     los filtros de liquidez, antigüedad y dilución quedan unas 60 elegibles.
   - **Indicadores:** momentum ajustado por volatilidad, fuerza relativa frente a BTC, calidad de tendencia,
     volumen anómalo y sobreextensión.
   - **Filtro de régimen de mercado:** BTC frente a su media de 200 días + % del mercado en tendencia. En
     régimen bajista no se abren posiciones.
2. **Agente investigador (Claude):** noticias, macro, desbloqueos de tokens, hacks, delistings y redes
   sociales, siempre con fuentes. Solo aporta hechos.
3. **Agente trader (Claude):** puede **vetar o reducir a la mitad** una operación con motivos concretos.
   **Nunca** puede ampliar el riesgo.
4. **Motor de riesgo (Python, límites duros):**
   - 1 % del capital en riesgo por operación.
   - Riesgo total abierto ≤ 4 % y máximo 5 posiciones.
   - Máximo 25 % del capital en una sola moneda.
   - Filtro de correlación.
   - Pausa si el capital cae un 15 % desde su máximo.
5. **Registro:** cinco carteras en paralelo para poder comparar:
   - **Real (tú):** lo que ejecutas de verdad.
   - **Q:** solo la parte cuant (simulada).
   - **QL:** cuant + agente trader (simulada). Mide si la IA aporta algo.
   - **RND:** entradas al azar con las mismas reglas (simulada). Es la referencia para saber si hay habilidad
     o solo suerte de mercado.
   - **V2C:** tendencia en BTC/ETH (simulada), con las reglas de la variante v2c:
     - Compra si el cierre diario está por encima de la media de 50 días y esta por encima de la de 200.
     - Vende si el cierre cae por debajo de la media de 50 días, si toca el stop a 3 ATR o a los 90 días.
     - Va aparte de la lista corta y del agente, y tiene su propia sección en el informe diario.

## Qué haces tú con una propuesta

El informe te da cada orden lista para copiar en Bitvavo:

1. **Compra con orden límite** al precio indicado, válida hasta la siguiente ejecución. Si no se ejecuta, cancélala.
2. **Nada más ejecutarse, protege la posición:**
   - Lo ideal es una orden **OCO** (objetivo + stop-limit) si tu app la ofrece.
   - Si no, pon al menos el **stop-loss límite**: Bitvavo bloquea el saldo, así que no puedes tener dos órdenes
     de venta a la vez. El sistema te avisará cuando se alcance el objetivo.
3. **Salida máxima a los 10 días:** si la posición sigue abierta, véndela. El sistema te lo recordará.

### Si no ejecutaste algo tal cual (importante)

Por defecto, el sistema asume que ejecutaste la compra si el precio llegó a tu límite. Si no fue así, edita
`ledger/live/fills.csv`. Se puede hacer desde la app o la web de GitHub en el móvil.

```csv
trade_id,estado,precio_entrada,cantidad,precio_salida,fecha_salida,notas
live-20260929T0547Z-AVAX,no_ejecutada,,,,,no me dio tiempo
live-20260929T0547Z-RENDER,ejecutada,1.6801,24.4,,,
live-20260929T0547Z-XPL,cerrada,0.0853,273,0.0911,2026-10-02T10:15:00+00:00,vendí antes
```

- `estado` puede ser `ejecutada`, `no_ejecutada` o `cerrada`.
- Usa **punto** como separador decimal.
- El `trade_id` aparece en cada propuesta del informe.

Así el registro real refleja lo que de verdad hiciste, y además **medimos si tu criterio personal mejora o
empeora el sistema**.

## Subir de capital por etapas (recomendado)

| Etapa | Capital real | Condición para pasar a la siguiente |
|---|---|---|
| 1 | 500 € | Al menos 30 operaciones sin problemas operativos y slippage real no peor que el simulado |
| 2 | 1.000–1.500 € | Al menos 100 operaciones y 12 semanas; R medio neto > 0 con el límite inferior del intervalo de confianza > 0; mejor que RND y que BTC ajustado por riesgo |
| 3 | Importe completo | Seguir revisando cada semana |

Para cambiarlo, edita `capital.live_eur` en `config/settings.yaml`. Para pausar el dinero real sin parar el
sistema, pon `mode.live_enabled: false`.

## Ajustes sin programar

Todo está en `config/settings.yaml`, comentado en español: capital, riesgo por operación, límites, filtros
de universo, stop y objetivo en ATR, horarios. `config/exclusions.yaml` sirve para excluir monedas a mano.

## Poner en marcha las ejecuciones diarias (Routines)

Las ejecuciones programadas son **Routines de Claude Code**. Las creas tú una vez desde
<https://claude.ai/code/routines>, con el repositorio adjunto. **Todos los pasos y los textos exactos
para copiar están en [`agents/ROUTINES.md`](agents/ROUTINES.md).** Resumen:

- **«Trading · principal»**, cada día a las 07:47 (Madrid). Es la importante.
- **«Trading · vigilancia»**, a las 13:47 y a las 20:47. Mientras el modo real esté desactivado no cambia los
  resultados simulados, así que es opcional.
- **Entorno:** el mismo de esta sesión («Marketing»), no «Default».
- **Conectores:** **ninguno**.
- **Datos:** los resultados se guardan en la rama `claude/trading-runs`.
- **Coste:** cada ejecución principal consume unos 143.000 tokens de tu plan (medido en una prueba), y hay
  un tope diario de ejecuciones de Routines por cuenta.

**Privacidad:** este repositorio debe ser **privado**. Los registros de carteras y, con el modo real, tus
operaciones y tu capital no deben ser públicos. Nunca se guardan credenciales en el repo.

## Agente investigador y redes sociales (Agent-Reach)

El agente investigador usa [Agent-Reach](https://github.com/Panniantong/Agent-Reach), con tu autorización del
28-09-2026.

**Qué se instala:**
- `scripts/setup_agent_reach.sh`, en cada ejecución (paso 0 de `agents/RUNBOOK.md`).
- Agent-Reach v1.5.0, con el commit `f65526c` verificado.
- twitter-cli 0.8.5, rdt-cli fijado y mcporter 0.14.1.
- Todo en carpetas del usuario, sin `sudo`, sin `--system`, sin instalaciones globales y sin
  actualizaciones automáticas.

**Qué funciona:**
- **Sin configurar nada:**
  - Búsqueda web Exa.
  - Lectura de artículos con Jina.
  - RSS.
- **X y Reddit necesitan cookies de cuentas secundarias**, creadas solo para esto, nunca las personales. Leer
  con cookies va contra las condiciones de esas plataformas y la cuenta puede ser bloqueada.
- **Solo lectura:** el agente tiene prohibido publicar, comentar, dar «me gusta» o seguir.

**Para activar X y Reddit**, añade estas variables en la configuración del entorno cloud: menú del entorno en la
barra de título de la sesión → *Edit*. **Nunca las pegues en el chat.**
- `TWITTER_AUTH_TOKEN` y `TWITTER_CT0`: las cookies `auth_token` y `ct0` de x.com.
- `REDDIT_SESSION`: la cookie `reddit_session` de reddit.com.

Se copian con la extensión *Cookie-Editor* del navegador: abre x.com o reddit.com con la cuenta secundaria →
Cookie-Editor → busca la cookie → copia su valor. Si no las añades, el agente anota «canal no disponible» y
sigue con el resto de fuentes.

## Impuestos (España)

- Cada venta, y también cada cambio de una cripto por otra, genera una ganancia o pérdida en la base del
  ahorro del IRPF (tramos desde el 19 %, método FIFO).
- Desde 2026, con la directiva DAC8, los exchanges informan a Hacienda de tus operaciones.
- El sistema opera siempre contra EUR y guarda todo en `ledger/live/trades.csv`.
- Consulta con un asesor fiscal.

## Para quien quiera mirar el código

```bash
pip install -e ".[dev]"
python -m trading scan          # escáner + carteras Q/RND/V2C + brief para los agentes
python -m trading decide        # aplica decisiones de los agentes y genera propuestas e informe
python -m trading monitor       # vigilancia
python -m trading weekly        # informe semanal
python -m trading backtest --from 2021-01-01
python -m trading research      # variantes pre-registradas (research/hypotheses.md)
python -m trading status
python -m pytest
```

| Ruta | Contenido |
|---|---|
| `src/trading/` | Datos, indicadores, puntuación, riesgo, simulación, backtest e informes |
| `agents/` | Cómo crear las Routines (`ROUTINES.md`), su procedimiento (`RUNBOOK.md`) y la especificación de los agentes |
| `research/` | Hipótesis y criterios pre-registrados antes de cada investigación |
| `runs/` | Todo lo que vio y decidió cada ejecución (auditable) |
| `ledger/` | Operaciones y capital de cada cartera |
| `reports/` | Informes diarios, semanales y del backtest |
| `data_cache/` | Histórico descargado (no se sube al repo) |

Fuentes de datos, todas públicas y gratuitas:
- Binance (`data-api.binance.vision` y `data.binance.vision`)
- Bitvavo
- CoinGecko
- OKX (funding)
- FRED
- alternative.me (Fear & Greed)
- RSS de CoinDesk, Cointelegraph y Decrypt
