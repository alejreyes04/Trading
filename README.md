# RegimeScanner v2.1 — Detector factorizado de 8 regímenes con IA embebida (ONNX) en MQL5

Licencia: **MIT** (ver `LICENSE`). Pensado para publicarse (open source) y para ser consumido desde EAs de MT5.

Indicador de MetaTrader 5 (MQL5) que clasifica el mercado en **8 regímenes** combinando un ensemble estadístico transparente con **dos modelos ONNX** entrenados en Python (la "IA que vive dentro del indicador"). La v2.1 añade un **etiquetador por destilación HMM** que mejora las etiquetas en las **fronteras ambiguas** de cada eje.

## Taxonomía de regímenes

Dos ejes ortogonales + dos estados especiales de volatilidad:

| # | Régimen | Composición | Color | Estrategia natural |
|---|---|---|---|---|
| 0 | BULL_QUIET | UP × QUIET | Lime | Trend-following (óptimo) |
| 1 | BULL_VOL | UP × VOLATILE | SeaGreen | TF con riesgo reducido |
| 2 | BEAR_QUIET | DOWN × QUIET | Tomato | TF corto (óptimo) |
| 3 | BEAR_VOL | DOWN × VOLATILE | FireBrick | TF corto, riesgo reducido |
| 4 | RANGE_QUIET | NONE × QUIET | Silver | Mean reversion |
| 5 | RANGE_VOL | NONE × VOLATILE | Goldenrod | Whipsaw: reducir/evitar |
| 6 | BREAKOUT | vol EXPANSION | DeepSkyBlue | Estrategias de ruptura |
| 7 | CRISIS | vol CRISIS | Magenta | Risk-off |

**Por qué factorizado y no un softmax plano de 8 clases** (esto responde a "preciso y fiable"): con 8 clases planas, las fronteras se entrenan con pocas muestras cada una y las clases raras (CRISIS, BREAKOUT) se ahogan. Factorizando, dirección (3 clases) y volatilidad (4 clases) son problemas más simples y mejor muestreados; la conjunta `p_dir ⊗ p_vol` produce 8 probabilidades que **suman 1 por construcción**, y un error en un eje no destruye el otro.

## Estructura del repositorio

```
MQL5/
  Include/RegimeFeatures.mqh        Motor de 12 features + ensemble factorizado (CONTRATO)
  Include/RegimeReader.mqh          API de consumo para EAs (wrapper de iCustom)
  Indicators/RegimeScanner.mq5      Indicador: ensemble + dual ONNX, histéresis, 8 colores
  Scripts/ExportRegimeFeatures.mq5  Exporta el dataset de entrenamiento a CSV
  Experts/ExampleRegimeEA.mq5       Esqueleto de EA con enrutado de módulos por régimen
python/
  train_regime_onnx.py              Ruta A: etiquetado forward (sklearn) -> ONNX
  label_regime_hmm.py               Ruta B (v2.1): teacher HMM -> CSV con posteriores suaves
  distill_regime_kl.py              Ruta B (v2.1): destilación KL (PyTorch) -> ONNX drop-in
  verify_onnx_contract.py           Verifica el contrato ONNX SIN MT5 (onnxruntime)
  requirements.txt                  Dependencias Python
```

## Las 12 features (contrato v2, orden fijo)

`er, adx_norm, slope_norm, r2, hurst, ac1, vr, atr_pct, atr_ratio, sqz_pct, skew, shock`.
Este orden es **contrato**: idéntico en `RegimeFeatures.mqh`, el CSV de export y todos los scripts Python. No reordenar sin re-exportar y re-entrenar.

## Dos rutas de entrenamiento de la IA

Ambas producen los mismos artefactos (`regime_dir.onnx`, `regime_vol.onnx`) con **firma idéntica**, así que el indicador las consume sin cambios.

### Ruta A — etiquetado forward (sklearn), rápida
```
python train_regime_onnx.py regime_features.csv
```
Etiquetas por umbrales forward-looking (SNR, cuantiles de vol), MLP + StandardScaler, export con `skl2onnx`.

### Ruta B — destilación HMM→KL (v2.1), mejor en fronteras ambiguas
```
python label_regime_hmm.py  regime_features.csv --out regime_labeled.csv
python distill_regime_kl.py regime_labeled.csv
```
1. `label_regime_hmm.py` ajusta **dos HMM Gaussianos por eje** (dirección 3 estados, volatilidad 4 estados) y escribe posteriores **suaves** `γ` por barra. El prior temporal (matriz de transición) evita que una excursión de una barra voltee la etiqueta: la señal en la frontera deja de parpadear.
2. `distill_regime_kl.py` entrena un *student* (PyTorch) minimizando
   `α·KL(student ‖ γ_HMM) + (1-α)·CE(student, etiqueta_forward)`:
   el término KL suaviza las fronteras, la CE ancla el significado económico.
   El `StandardScaler` se **hornea dentro del grafo ONNX** (el indicador alimenta features crudas) y se replica la firma de dos salidas `label(int64) + probabilities(float32)` que exige `OnnxRun`.

### Verificación del contrato (sin MetaTrader)
```
python verify_onnx_contract.py                          # dry-run + firma ONNX
python verify_onnx_contract.py --dir regime_dir.onnx --vol regime_vol.onnx
```
Comprueba en `onnxruntime` que los `.onnx` tienen salidas `['label','probabilities']`, formas `[N,3]/[N,4]`, filas que suman 1, y `label==argmax`; y vuelca `golden_vectors.txt` para un print-compare puntual en MQL5.

## Pipeline completo

1. Copia el árbol `MQL5\` a la carpeta de datos de MT5. Compila indicador, script y EA. El indicador **ya funciona** con el ensemble factorizado transparente (sin ONNX).
2. Ejecuta `ExportRegimeFeatures` sobre el símbolo/timeframe objetivo (≥10k barras recomendado) → `MQL5\Files\regime_features.csv`.
3. Entrena por la **Ruta A** o la **Ruta B** (recomendada para fronteras) → `regime_dir.onnx`, `regime_vol.onnx`.
4. `python verify_onnx_contract.py --dir regime_dir.onnx --vol regime_vol.onnx`.
5. Copia ambos `.onnx` a `MQL5\Files\` y activa `InpUseONNX=true`. El fallback es **por eje**: si falta un modelo, ese eje usa el ensemble y el otro sigue con ONNX.
6. En tu EA: `#include <RegimeReader.mqh>` y usa la API (ver `ExampleRegimeEA.mq5`).

## Contrato de buffers (estable — para iCustom directo)

Buffer 0 = confianza · Buffer 1 = régimen confirmado 0–7 (color index) · Buffers 2–9 = P(régimen 0..7), suman 1. Orden de inputs de `iCustom`: `er, slope_win, hurst_win, atr_rank_win, vr_q, use_onnx, dir_model, vol_model, min_prob, confirm_bars, max_history, alerts` (RegimeReader ya lo encapsula).

## Contrato ONNX (para reproducirlo con cualquier framework)

- Entrada: `float32 [N, 12]` (batch dinámico), **features crudas** (el scaler va horneado).
- Salida 0: `label int64 [N]`. Salida 1: `probabilities float32 [N, n]` (`n`=3 dir, 4 vol).
- Orden de clases: dir `{UP, DOWN, NONE}`; vol `{QUIET, VOLATILE, EXPANSION, CRISIS}`.

## Limitaciones honestas (léelas antes de operar con esto)

- El régimen sólo es inequívoco **ex-post**. La histéresis (`InpMinProb`, `InpConfirmBars`) cambia lag por estabilidad: es un parámetro de diseño de tu estrategia, no un defecto.
- Con 8 clases, `InpMinProb` opera sobre la **conjunta** (máximos típicos 0.3–0.6). Por eso el default es 0.30.
- CRISIS y EXPANSION son raras por definición; con poca historia su recall no es fiable. BREAKOUT es **transitorio** (dura pocas barras y transiciona): es correcto, no un bug.
- Los umbrales/HMM **definen** la verdad: son una convención razonable, no una ley física. Documenta la definición que uses.
- Modelos específicos por símbolo y timeframe. Re-entrenamiento walk-forward periódico.
- **Regla de despliegue**: cada eje debe superar su baseline de clase mayoritaria en *balanced accuracy*, y el gating debe mejorar la estrategia en el tester (misma estrategia con/sin gating). Si sólo empata con el ensemble, quédate con el ensemble: un modelo que empata sólo añade opacidad.

## Roadmap

- [x] Destilación HMM→clasificadores por eje (v2.1)
- [ ] Calibración de probabilidades (isotónica/Platt) por eje
- [ ] Matriz de transición del HMM como *prior* bayesiano de la conjunta (menos whipsaw sin lag fijo)
- [ ] Features de sesión y multi-timeframe (contrato v3)
- [ ] LightGBM vía onnxmltools si el MLP satura

## Licencia

MIT — ver `LICENSE`. EL USO EN TRADING REAL ES BAJO TU EXCLUSIVA RESPONSABILIDAD.
