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

## Cómo funciona (cada día, sin que tengas que programar nada)

| Hora (Madrid) | Qué ocurre | Te llega |
|---|---|---|
| ~07:47 | **Ejecución principal:** escáner cuant → agente investigador → agente trader → motor de riesgo | Notificación con las compras propuestas + informe en `reports/` |
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
5. **Registro:** cuatro carteras en paralelo para poder comparar:
   - **Real (tú):** lo que ejecutas de verdad.
   - **Q:** solo la parte cuant (simulada).
   - **QL:** cuant + agente trader (simulada). Mide si la IA aporta algo.
   - **RND:** entradas al azar con las mismas reglas (simulada). Es la referencia para saber si hay habilidad
     o solo suerte de mercado.

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

## Agente investigador y redes sociales (Agent-Reach)

El agente investigador está preparado para usar [Agent-Reach](https://github.com/Panniantong/Agent-Reach)
(X/Twitter, Reddit, Exa, Jina, YouTube, RSS). **Todavía no está instalado:** el sistema de permisos de esta
sesión bloqueó instalar código de terceros sin tu autorización explícita. Mientras tanto, el agente usa
búsqueda web, lectura de páginas y RSS.

Si decides activarlo, te pediré:

1. Tu autorización explícita para instalar Agent-Reach v1.5.0 (fijado al commit `f65526c`) y, con versión
   fijada, twitter-cli 0.8.5, rdt-cli y mcporter. Todo sin `--system` ni sudo.
2. **Cuentas secundarias** de X y Reddit creadas solo para esto, nunca las personales. Leer con cookies va
   contra las condiciones de esas plataformas y la cuenta puede ser bloqueada.
3. Añadir estas variables de entorno en la configuración del entorno cloud (menú del entorno en la barra de
   título de la sesión → Editar). **Nunca las pegues en el chat.**
   - `TWITTER_AUTH_TOKEN` y `TWITTER_CT0`: las cookies `auth_token` y `ct0` de x.com.
   - `REDDIT_SESSION`: la cookie `reddit_session` de reddit.com.

   Se copian con la extensión *Cookie-Editor* del navegador (abre x.com o reddit.com con la cuenta
   secundaria → Cookie-Editor → busca la cookie → copia su valor).

## Impuestos (España)

- Cada venta, y también cada cambio de una cripto por otra, genera una ganancia o pérdida en la base del
  ahorro del IRPF (tramos desde el 19 %, método FIFO).
- Desde 2026, con la directiva DAC8, los exchanges informan a Hacienda de tus operaciones.
- El sistema opera siempre contra EUR y guarda todo en `ledger/live/trades.csv`.
- Consulta con un asesor fiscal.

## Para quien quiera mirar el código

```bash
pip install -e ".[dev]"
python -m trading scan          # escáner + carteras Q/RND + brief para los agentes
python -m trading decide        # aplica decisiones de los agentes y genera propuestas e informe
python -m trading monitor       # vigilancia
python -m trading weekly        # informe semanal
python -m trading backtest --from 2021-01-01
python -m trading status
python -m pytest
```

| Ruta | Contenido |
|---|---|
| `src/trading/` | Datos, indicadores, puntuación, riesgo, simulación, backtest e informes |
| `agents/` | Procedimiento de las Routines (`RUNBOOK.md`) y especificación de los agentes |
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
