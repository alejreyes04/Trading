"""
label_regime_hmm.py — RegimeScanner v2.1 (destilación HMM)
=========================================================
SPDX-License-Identifier: MIT

Etiquetador *teacher* por HMM. Ajusta DOS HMM Gaussianos factorizados (uno por
eje) sobre las features exportadas desde MT5 (ExportRegimeFeatures.mq5) y escribe
un CSV AUMENTADO con los posteriores suaves (gamma) y las etiquetas duras Viterbi
de cada eje. Ese CSV alimenta la destilación KL (distill_regime_kl.py).

Por qué HMM y no umbrales forward duros
---------------------------------------
El etiquetado de train_regime_onnx.py usa cortes forward duros (SNR, cuantiles).
En la frontera (SNR≈1.0, vol≈cuantil) el corte binario parpadea barra a barra y
el clasificador acaba ajustando ruido justo donde el régimen es ambiguo. Un HMM
añade un PRIOR TEMPORAL (matriz de transición): una excursión de una sola barra
no voltea el estado, y el posterior gamma da una pertenencia SUAVE en la frontera.
Esos gamma son mejores objetivos de destilación que una etiqueta 0/1.

Rol causal (importante, no lo "arregles")
------------------------------------------
El HMM se usa SÓLO para DEFINIR etiquetas (igual que las etiquetas forward-looking
actuales). Por eso el suavizado full-sample de Baum-Welch es LEGÍTIMO aquí. Las
features que consume el modelo en inferencia siguen siendo estrictamente causales
(CRegimeFeatures.Compute). No conviertas esto en un filtro causal: perderías
justamente el beneficio en fronteras.

Contrato de clases (idéntico a RegimeFeatures.mqh / train_regime_onnx.py)
    dir : {UP, DOWN, NONE}
    vol : {QUIET, VOLATILE, EXPANSION, CRISIS}

Instalación:
    pip install -r requirements.txt          # incluye hmmlearn

Uso:
    python label_regime_hmm.py regime_features.csv
    python label_regime_hmm.py regime_features.csv --out regime_labeled.csv \
                               --restarts 8 --seed 42
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

# --------------------------------------------------------------- contrato v2
# Orden fijo de las 12 features (mismo que RegimeFeatures.mqh y el CSV).
FEATURES = ["er", "adx_norm", "slope_norm", "r2", "hurst", "ac1", "vr",
            "atr_pct", "atr_ratio", "sqz_pct", "skew", "shock"]

DIR_NAMES = ["UP", "DOWN", "NONE"]
VOL_NAMES = ["QUIET", "VOLATILE", "EXPANSION", "CRISIS"]

# Subconjuntos de observación por eje (se referencian por NOMBRE, nunca por
# posición: así el orden del contrato de 12 features no puede romper esto).
DIR_OBS = ["slope_norm", "er", "r2", "ac1", "adx_norm"]     # + logret
VOL_OBS = ["atr_pct", "atr_ratio", "sqz_pct", "shock", "vr"]  # + |logret|


# --------------------------------------------------------------- utilidades
def _zscore(X: np.ndarray) -> np.ndarray:
    """Estandariza columnas (solo para estabilidad numérica del HMM teacher;
    no afecta al scaler que se hornea en el modelo student)."""
    mu = X.mean(axis=0, keepdims=True)
    sd = X.std(axis=0, keepdims=True)
    sd = np.where(sd > 1e-12, sd, 1.0)
    return (X - mu) / sd


def load_features(path: str) -> pd.DataFrame:
    """Lee el CSV de ExportRegimeFeatures; valida columnas; deriva logret."""
    df = pd.read_csv(path).dropna().reset_index(drop=True)
    missing = [c for c in FEATURES + ["close"] if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas en el CSV: {missing}")
    logc = np.log(df["close"].to_numpy(float))
    ret = np.empty(len(df), dtype=float)
    ret[0] = 0.0
    ret[1:] = np.diff(logc)                 # ret[i] = log(close[i]/close[i-1])
    df["logret"] = ret
    return df


def build_dir_obs(df: pd.DataFrame) -> np.ndarray:
    X = df[DIR_OBS].to_numpy(float)
    X = np.column_stack([X, df["logret"].to_numpy(float)])
    return _zscore(X)


def build_vol_obs(df: pd.DataFrame) -> np.ndarray:
    X = df[VOL_OBS].to_numpy(float)
    X = np.column_stack([X, np.abs(df["logret"].to_numpy(float))])
    return _zscore(X)


def fit_hmm(X: np.ndarray, n_states: int, n_restarts: int, seed: int):
    """GaussianHMM con covarianza diagonal. Best-of-N restarts por
    log-verosimilitud → resultado reproducible y menos sensible al init."""
    from hmmlearn.hmm import GaussianHMM

    best_model, best_score = None, -np.inf
    for r in range(n_restarts):
        model = GaussianHMM(n_components=n_states, covariance_type="diag",
                            n_iter=200, tol=1e-3, random_state=seed + r)
        try:
            model.fit(X)
            score = model.score(X)
        except Exception as exc:                       # pragma: no cover
            print(f"  restart {r}: fallo ({exc})")
            continue
        if score > best_score:
            best_score, best_model = score, model
    if best_model is None:
        raise RuntimeError("Ningún restart del HMM convergió.")
    print(f"  mejor log-verosimilitud: {best_score:.1f} "
          f"({n_restarts} restarts, {n_states} estados)")
    return best_model


def _state_key(model, X: np.ndarray, key_col: int) -> np.ndarray:
    """Media de la observación 'key_col' por estado (para ordenar/alinear)."""
    states = model.predict(X)
    means = np.array([X[states == s, key_col].mean() if np.any(states == s)
                      else model.means_[s, key_col]
                      for s in range(model.n_components)])
    return means


def align_dir_states(model, X: np.ndarray) -> list[int]:
    """3 estados -> permutación a orden semántico [UP, DOWN, NONE].
    Clave: media de logret (última columna). Mayor->UP, menor->DOWN, medio->NONE.
    Desempate determinista por índice de estado."""
    key = _state_key(model, X, key_col=X.shape[1] - 1)      # logret
    order = np.lexsort((np.arange(len(key)), key))          # asc, tie->idx
    down, none, up = order[0], order[1], order[2]
    return [int(up), int(down), int(none)]                  # -> [UP,DOWN,NONE]


def align_vol_states(model, X: np.ndarray) -> list[int]:
    """4 estados -> permutación a orden semántico [QUIET,VOLATILE,EXPANSION,
    CRISIS]. Clave: score de volatilidad = media de |logret| (última columna).
    Escalera ordinal ascendente. Desempate por índice."""
    key = _state_key(model, X, key_col=X.shape[1] - 1)      # |logret|
    order = np.lexsort((np.arange(len(key)), key))          # asc
    quiet, vol, exp, crisis = order[0], order[1], order[2], order[3]
    return [int(quiet), int(vol), int(exp), int(crisis)]


def posteriors(model, X: np.ndarray, perm: list[int]) -> np.ndarray:
    """gamma reordenado a orden semántico de clase. Filas suman 1."""
    g = model.predict_proba(X)                              # (T, n_states)
    return g[:, perm]


def viterbi_labels(model, X: np.ndarray, perm: list[int]) -> np.ndarray:
    """Camino Viterbi mapeado a etiqueta semántica (0..n-1). Globalmente
    suavizado: mejor que argmax por barra en las fronteras."""
    inv = np.empty(len(perm), dtype=int)
    for sem, raw in enumerate(perm):
        inv[raw] = sem
    return inv[model.predict(X)]


# ------------------------------------------------------------------ pipeline
def label(df: pd.DataFrame, n_restarts: int, seed: int) -> pd.DataFrame:
    if "logret" not in df.columns:                      # robusto si se llama suelto
        logc = np.log(df["close"].to_numpy(float))
        ret = np.empty(len(df), dtype=float)
        ret[0] = 0.0
        ret[1:] = np.diff(logc)
        df = df.copy()
        df["logret"] = ret
    print("Ajustando HMM de DIRECCIÓN (3 estados)...")
    Xd = build_dir_obs(df)
    md = fit_hmm(Xd, 3, n_restarts, seed)
    pd_perm = align_dir_states(md, Xd)
    gdir = posteriors(md, Xd, pd_perm)
    ydir = viterbi_labels(md, Xd, pd_perm)

    print("Ajustando HMM de VOLATILIDAD (4 estados)...")
    Xv = build_vol_obs(df)
    mv = fit_hmm(Xv, 4, n_restarts, seed)
    pv_perm = align_vol_states(mv, Xv)
    gvol = posteriors(mv, Xv, pv_perm)
    yvol = viterbi_labels(mv, Xv, pv_perm)

    out = df.copy()
    out["pdir_up"], out["pdir_down"], out["pdir_none"] = gdir.T
    out["pvol_quiet"] = gvol[:, 0]
    out["pvol_vol"] = gvol[:, 1]
    out["pvol_exp"] = gvol[:, 2]
    out["pvol_crisis"] = gvol[:, 3]
    out["ydir_hmm"] = ydir.astype(np.int64)
    out["yvol_hmm"] = yvol.astype(np.int64)

    print("\nDistribución dir (Viterbi):",
          {DIR_NAMES[k]: int((ydir == k).sum()) for k in range(3)})
    print("Distribución vol (Viterbi):",
          {VOL_NAMES[k]: int((yvol == k).sum()) for k in range(4)})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Etiquetador teacher HMM (v2.1)")
    ap.add_argument("csv", nargs="?", default="regime_features.csv",
                    help="CSV de ExportRegimeFeatures")
    ap.add_argument("--out", default="regime_labeled.csv",
                    help="CSV aumentado de salida")
    ap.add_argument("--restarts", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    np.random.seed(args.seed)
    df = load_features(args.csv)
    if len(df) < 3000:
        print(f"AVISO: sólo {len(df)} filas. Para CRISIS/EXPANSION raras, "
              f"exporta más historia (>=10k barras).")
    out = label(df, args.restarts, args.seed)
    out.to_csv(args.out, index=False)
    print(f"\nOK: {len(out)} filas -> {args.out}")
    print("Siguiente paso: python distill_regime_kl.py " + args.out)


if __name__ == "__main__":
    sys.exit(main())
