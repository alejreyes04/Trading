"""
train_regime_onnx.py — RegimeScanner v2.0 (8 regímenes)
========================================================
SPDX-License-Identifier: MIT

Entrena DOS clasificadores factorizados sobre las features exportadas desde
MT5 (ExportRegimeFeatures.mq5) y los exporta a ONNX para RegimeScanner.mq5:

    regime_dir.onnx  ->  eje DIRECCIÓN   (3 clases: UP / DOWN / NONE)
    regime_vol.onnx  ->  eje VOLATILIDAD (4 clases: QUIET / VOLATILE /
                                                    EXPANSION / CRISIS)

Por qué factorizado y no un softmax plano de 8 clases:
- Cada eje es un problema más simple con más muestras por frontera.
- CRISIS y EXPANSION son clases raras: en un modelo de 8 clases se ahogan;
  en el eje de volatilidad (4 clases + oversampling) tienen oportunidad.
- La composición p_dir ⊗ p_vol produce 8 probabilidades que suman 1 por
  construcción (misma composición implementada en RegimeFeatures.mqh).

Instalación:
    pip install pandas numpy scikit-learn skl2onnx onnx

Uso:
    python train_regime_onnx.py regime_features.csv

Salida: regime_dir.onnx + regime_vol.onnx -> copiar a <DataFolder>\\MQL5\\Files\\
        y activar InpUseONNX=true en el indicador.
"""

import sys
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

# ---------------------------------------------------------------- config
FEATURES = ["er", "adx_norm", "slope_norm", "r2", "hurst", "ac1", "vr",
            "atr_pct", "atr_ratio", "sqz_pct", "skew", "shock"]

DIR_NAMES = ["UP", "DOWN", "NONE"]
VOL_NAMES = ["QUIET", "VOLATILE", "EXPANSION", "CRISIS"]
REG_NAMES = ["BULL_QUIET", "BULL_VOL", "BEAR_QUIET", "BEAR_VOL",
             "RANGE_QUIET", "RANGE_VOL", "BREAKOUT", "CRISIS"]

H = 24              # horizonte forward para etiquetar (barras del TF exportado)
SNR_TH = 1.0        # significancia de tendencia forward
R2_TH = 0.30        # calidad mínima del ajuste lineal forward
Q_QUIET = 0.40      # fvol < percentil -> QUIET
Q_CRISIS = 0.965    # fvol > percentil -> CRISIS
Q_TRAIL_LOW = 0.40  # vol pasada baja (condición de squeeze previo)
Q_FWD_HIGH = 0.70   # vol futura alta (condición de expansión)
TEST_FRAC = 0.30
EMBARGO = H
SEED = 42
MIN_CLASS_FRAC = 0.25   # oversampling: cada clase >= 25% de la mayoritaria


# ------------------------------------------------------------- etiquetado
def make_axis_labels(close: np.ndarray):
    """Devuelve (ydir, yvol) por barra, -1 donde no hay futuro/pasado
    suficiente. Etiquetas forward-looking (definen la verdad); las
    features son estrictamente causales — nunca mezclar ambos roles.
    """
    n = len(close)
    logc = np.log(close)
    ret = np.diff(logc)                       # ret[i] = logc[i+1] - logc[i]

    # --- ventanas futuras -------------------------------------------------
    rw = sliding_window_view(ret, H)          # fila i = H retornos futuros de i
    fvol = rw.std(axis=1, ddof=0)             # vol realizada forward
    net = logc[H:] - logc[:-H]
    snr = np.divide(net, fvol * np.sqrt(H),
                    out=np.zeros_like(net), where=fvol > 0)

    cw = sliding_window_view(logc[1:], H)     # log-closes futuros
    x = np.arange(H, dtype=float)
    xc = x - x.mean()
    denx = float((xc ** 2).sum())
    cw_c = cw - cw.mean(axis=1, keepdims=True)
    slope = (cw_c @ xc) / denx
    ss_tot = (cw_c ** 2).sum(axis=1)
    r2 = np.divide(slope ** 2 * denx, ss_tot,
                   out=np.zeros_like(slope), where=ss_tot > 0)

    # --- vol realizada TRASERA (para detectar transición squeeze->exp.) ---
    # tvol[i] = std(ret[i-H .. i-1]) = fvol desplazada H barras.
    # Definida para i >= H; NaN antes (esas barras no pueden ser EXPANSION).
    tvol_full = np.full(n, np.nan)
    tvol_full[H:H + len(fvol)] = fvol

    # --- umbrales (estadísticos in-sample para la DEFINICIÓN del régimen;
    #     documentado y asumido: definen la verdad, no el modelo) ----------
    q_quiet = np.quantile(fvol, Q_QUIET)
    q_crisis = np.quantile(fvol, Q_CRISIS)
    q_fwd_hi = np.quantile(fvol, Q_FWD_HIGH)
    tv_valid = tvol_full[~np.isnan(tvol_full)]
    q_trail_lo = np.quantile(tv_valid, Q_TRAIL_LOW)

    # --- eje dirección -----------------------------------------------------
    ydir = np.full(n, -1, dtype=np.int64)
    d = np.full(n - H, 2, dtype=np.int64)                 # NONE por defecto
    d[(snr > SNR_TH) & (r2 > R2_TH)] = 0                  # UP
    d[(snr < -SNR_TH) & (r2 > R2_TH)] = 1                 # DOWN
    ydir[: n - H] = d

    # --- eje volatilidad ----------------------------------------------------
    yvol = np.full(n, -1, dtype=np.int64)
    v = np.where(fvol < q_quiet, 0, 1).astype(np.int64)   # QUIET / VOLATILE
    # EXPANSION: vol pasada comprimida Y vol futura alta (transición)
    tv = tvol_full[: n - H]
    exp_mask = (~np.isnan(tv)) & (tv < q_trail_lo) & (fvol > q_fwd_hi)
    v[exp_mask] = 2
    # CRISIS: cola extrema de vol futura (prioridad máxima)
    v[fvol > q_crisis] = 3
    yvol[: n - H] = v

    return ydir, yvol


def compose(dir_lab: np.ndarray, vol_lab: np.ndarray) -> np.ndarray:
    """Misma composición que RegimeFeatures.mqh: (dir, vol) -> régimen 0..7."""
    reg = np.empty_like(dir_lab)
    special = vol_lab >= 2
    reg[special] = 4 + vol_lab[special]                    # 6 BREAKOUT, 7 CRISIS
    base = ~special
    d = dir_lab[base]
    v = vol_lab[base]
    reg[base] = np.where(d == 2, 4, d * 2) + v             # 0..5
    return reg


def oversample(X: np.ndarray, y: np.ndarray, rng: np.random.Generator):
    """Oversampling simple de clases minoritarias hasta MIN_CLASS_FRAC de la
    mayoritaria. Necesario porque CRISIS/EXPANSION son raras por definición
    y MLPClassifier no soporta class_weight. SOLO se aplica al train."""
    counts = np.bincount(y, minlength=int(y.max()) + 1)
    target = int(counts.max() * MIN_CLASS_FRAC)
    idx_all = [np.arange(len(y))]
    for k, c in enumerate(counts):
        if 0 < c < target:
            extra = rng.choice(np.where(y == k)[0], size=target - c, replace=True)
            idx_all.append(extra)
    idx = np.concatenate(idx_all)
    rng.shuffle(idx)
    return X[idx], y[idx]


def build_pipe():
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.neural_network import MLPClassifier
    return Pipeline([
        ("scaler", StandardScaler()),
        ("mlp", MLPClassifier(hidden_layer_sizes=(32, 16),
                              max_iter=600,
                              early_stopping=True,
                              n_iter_no_change=20,
                              random_state=SEED)),
    ])


def export_onnx(pipe, n_features: int, out_path: str) -> None:
    from skl2onnx import convert_sklearn
    from skl2onnx.common.data_types import FloatTensorType
    onx = convert_sklearn(
        pipe,
        initial_types=[("input", FloatTensorType([None, n_features]))],
        # zipmap=False -> salida 'probabilities' como tensor float32
        # (contrato con LoadAxisModel() en RegimeScanner.mq5)
        options={id(pipe.steps[-1][1]): {"zipmap": False}},
        target_opset=15,
    )
    with open(out_path, "wb") as fh:
        fh.write(onx.SerializeToString())


# ------------------------------------------------------------------ main
def main(path: str) -> None:
    from sklearn.metrics import (classification_report, confusion_matrix,
                                 balanced_accuracy_score)

    rng = np.random.default_rng(SEED)
    df = pd.read_csv(path).dropna().reset_index(drop=True)
    if len(df) < 3000:
        print(f"AVISO: sólo {len(df)} filas. Para 4 clases de volatilidad con "
              f"CRISIS/EXPANSION raras, exporta más historia (>=10k barras).")

    ydir, yvol = make_axis_labels(df["close"].to_numpy(float))
    df["ydir"], df["yvol"] = ydir, yvol
    df = df[(df["ydir"] >= 0) & (df["yvol"] >= 0)].reset_index(drop=True)

    X = df[FEATURES].to_numpy(np.float32)
    yd = df["ydir"].to_numpy(np.int64)
    yv = df["yvol"].to_numpy(np.int64)

    n = len(df)
    n_test = int(n * TEST_FRAC)
    n_train = n - n_test - EMBARGO
    Xtr, Xte = X[:n_train], X[n_train + EMBARGO:]
    ydtr, ydte = yd[:n_train], yd[n_train + EMBARGO:]
    yvtr, yvte = yv[:n_train], yv[n_train + EMBARGO:]

    print(f"Filas: {n} | train: {n_train} | embargo: {EMBARGO} | test: {len(Xte)}")
    print("Distribución dir (train):",
          {DIR_NAMES[k]: int((ydtr == k).sum()) for k in range(3)})
    print("Distribución vol (train):",
          {VOL_NAMES[k]: int((yvtr == k).sum()) for k in range(4)})
    for k in range(4):
        if (yvtr == k).sum() < 100:
            print(f"AVISO: clase {VOL_NAMES[k]} con <100 muestras en train. "
                  f"El recall de esa clase será poco fiable; exporta más "
                  f"historia o relaja sus umbrales de etiquetado.")

    # ---------------- eje dirección ----------------
    pipe_dir = build_pipe()
    Xd, yD = oversample(Xtr, ydtr, rng)
    pipe_dir.fit(Xd, yD)
    pd_pred = pipe_dir.predict(Xte)
    print("\n========== EJE DIRECCIÓN (test cronológico) ==========")
    print(classification_report(ydte, pd_pred, target_names=DIR_NAMES,
                                zero_division=0))
    print(f"Balanced accuracy: {balanced_accuracy_score(ydte, pd_pred):.3f}")

    # ---------------- eje volatilidad ----------------
    pipe_vol = build_pipe()
    Xv, yV = oversample(Xtr, yvtr, rng)
    pipe_vol.fit(Xv, yV)
    pv_pred = pipe_vol.predict(Xte)
    print("\n========== EJE VOLATILIDAD (test cronológico) ==========")
    print(classification_report(yvte, pv_pred, target_names=VOL_NAMES,
                                zero_division=0))
    print(f"Balanced accuracy: {balanced_accuracy_score(yvte, pv_pred):.3f}")

    # ---------------- 8 regímenes compuestos ----------------
    true8 = compose(ydte, yvte)
    pred8 = compose(pd_pred, pv_pred)
    print("\n========== 8 REGÍMENES COMPUESTOS (test) ==========")
    print(classification_report(true8, pred8, labels=list(range(8)),
                                target_names=REG_NAMES, zero_division=0))
    print("Matriz de confusión (filas=real, cols=pred):")
    print(confusion_matrix(true8, pred8, labels=list(range(8))))

    majd = int(np.bincount(ydtr).argmax())
    majv = int(np.bincount(yvtr).argmax())
    print(f"\nBaselines de clase mayoritaria: dir 'siempre {DIR_NAMES[majd]}' "
          f"= {(ydte == majd).mean():.3f} | vol 'siempre {VOL_NAMES[majv]}' "
          f"= {(yvte == majv).mean():.3f} (accuracy simple)")
    print("REGLA DE DECISIÓN: cada eje debe superar con claridad su baseline")
    print("en balanced accuracy, y el conjunto debe mejorar al ensemble del")
    print("indicador en walk-forward. Si no, NO despliegues los modelos: un")
    print("modelo que empata con la heurística sólo añade opacidad.")

    # ---------------- export ----------------
    export_onnx(pipe_dir, len(FEATURES), "regime_dir.onnx")
    export_onnx(pipe_vol, len(FEATURES), "regime_vol.onnx")
    print("\nModelos guardados: regime_dir.onnx, regime_vol.onnx")
    print(r"Cópialos a  <Carpeta de datos MT5>\MQL5\Files\  y activa "
          r"InpUseONNX=true.")
    print("Recuerda: modelos específicos del símbolo/timeframe exportado.")
    print("Re-entrena periódicamente (walk-forward, p. ej. mensual).")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "regime_features.csv")
