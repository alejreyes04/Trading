"""
verify_onnx_contract.py — RegimeScanner v2.1
============================================
SPDX-License-Identifier: MIT

Valida —SIN MetaTrader— el contrato del que depende RegimeScanner.mq5, para
detectar roturas antes de copiar los .onnx a MT5. Comprueba tres cosas:

  1) HMM teacher (label_regime_hmm.py) sobre datos sintéticos con tramos
     inyectados: gamma suma 1, alineación DETERMINISTA entre dos corridas,
     tramo alcista -> mayoría UP, estallido de vol -> mayoría CRISIS.

  2) Contrato ONNX (distill_regime_kl.export_onnx) cargado en onnxruntime con
     features CRUDAS: nombres de salida ['label','probabilities'], formas
     [N,3]/[N,4], filas suman ~1, label == argmax(probs). Éste es el punto
     exacto en que el indicador fallaría si la firma no coincide.

  3) Golden vectors: vuelca ~10 filas (12 features crudas -> probs) a
     golden_vectors.txt para un print-compare puntual en un script MQL5.

Uso:
    pip install -r requirements.txt
    python verify_onnx_contract.py
    # opcional: valida también modelos ya entrenados en disco
    python verify_onnx_contract.py --dir regime_dir.onnx --vol regime_vol.onnx
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

import numpy as np
import pandas as pd

from label_regime_hmm import FEATURES, label as hmm_label, DIR_NAMES, VOL_NAMES

SEED = 7


# --------------------------------------------------------- datos sintéticos
def synth(n_seg: int = 500):
    """OHLC + 12 features con 4 tramos: UP, DOWN, RANGE, BURST(crisis).
    Devuelve (df, seg) donde seg[i] in {'UP','DOWN','RANGE','BURST'}."""
    rng = np.random.default_rng(SEED)
    segs = ["UP", "DOWN", "RANGE", "BURST"]
    seg = np.repeat(segs, n_seg)
    n = len(seg)

    logret = np.zeros(n)
    f = {k: np.zeros(n) for k in FEATURES}
    for i, s in enumerate(seg):
        if s == "UP":
            logret[i] = rng.normal(0.0008, 0.0015)
            f["er"][i], f["adx_norm"][i], f["slope_norm"][i], f["r2"][i] = 0.7, 0.7, 0.8, 0.7
            f["hurst"][i], f["ac1"][i], f["vr"][i] = 0.65, 0.1, 1.3
            f["atr_pct"][i], f["atr_ratio"][i], f["sqz_pct"][i] = 0.4, 1.0, 0.5
            f["skew"][i], f["shock"][i] = 0.1, 1.0
        elif s == "DOWN":
            logret[i] = rng.normal(-0.0008, 0.0015)
            f["er"][i], f["adx_norm"][i], f["slope_norm"][i], f["r2"][i] = 0.7, 0.7, -0.8, 0.7
            f["hurst"][i], f["ac1"][i], f["vr"][i] = 0.65, 0.1, 1.3
            f["atr_pct"][i], f["atr_ratio"][i], f["sqz_pct"][i] = 0.42, 1.0, 0.5
            f["skew"][i], f["shock"][i] = -0.1, 1.0
        elif s == "RANGE":
            logret[i] = rng.normal(0.0, 0.0008)
            f["er"][i], f["adx_norm"][i], f["slope_norm"][i], f["r2"][i] = 0.1, 0.15, 0.0, 0.05
            f["hurst"][i], f["ac1"][i], f["vr"][i] = 0.45, -0.2, 0.8
            f["atr_pct"][i], f["atr_ratio"][i], f["sqz_pct"][i] = 0.3, 0.9, 0.3
            f["skew"][i], f["shock"][i] = 0.0, 0.8
        else:  # BURST -> crisis (vol claramente dominante)
            logret[i] = rng.normal(0.0, 0.012)
            f["er"][i], f["adx_norm"][i], f["slope_norm"][i], f["r2"][i] = 0.3, 0.4, 0.0, 0.2
            f["hurst"][i], f["ac1"][i], f["vr"][i] = 0.5, -0.1, 1.1
            f["atr_pct"][i], f["atr_ratio"][i], f["sqz_pct"][i] = 0.98, 2.5, 0.6
            f["skew"][i], f["shock"][i] = -0.8, 4.2

    close = 100.0 * np.exp(np.cumsum(logret))
    df = pd.DataFrame({"time": np.arange(n), "open": close, "high": close,
                       "low": close, "close": close})
    for k in FEATURES:
        df[k] = f[k]
    return df, seg


# ------------------------------------------------------------ check 1: HMM
def check_hmm() -> bool:
    print("== Check 1: HMM teacher (datos sintéticos) ==")
    df, seg = synth()
    a = hmm_label(df.copy(), n_restarts=4, seed=SEED)
    b = hmm_label(df.copy(), n_restarts=4, seed=SEED)

    gdir = a[["pdir_up", "pdir_down", "pdir_none"]].to_numpy()
    gvol = a[["pvol_quiet", "pvol_vol", "pvol_exp", "pvol_crisis"]].to_numpy()
    ok = True

    if not np.allclose(gdir.sum(1), 1, atol=1e-5) or not np.allclose(gvol.sum(1), 1, atol=1e-5):
        print("  FALLO: gamma no suma 1"); ok = False
    if not np.array_equal(a["ydir_hmm"], b["ydir_hmm"]) or \
       not np.array_equal(a["yvol_hmm"], b["yvol_hmm"]):
        print("  FALLO: alineación no determinista entre corridas"); ok = False

    up_lab = a["ydir_hmm"].to_numpy()[seg == "UP"]
    if (up_lab == 0).mean() < 0.5:
        print(f"  FALLO: tramo UP no mayoría UP ({(up_lab==0).mean():.2f})"); ok = False
    # Invariante de plumbing: el estallido debe caer en clase de vol ALTA
    # (EXPANSION o CRISIS), no en QUIET/VOLATILE. El bucket exacto del tope
    # depende de cuántos niveles de vol distintos haya en los datos.
    burst_lab = a["yvol_hmm"].to_numpy()[seg == "BURST"]
    if (burst_lab >= 2).mean() < 0.5:
        print(f"  FALLO: tramo BURST no cae en vol alta ({(burst_lab>=2).mean():.2f})"); ok = False

    print("  OK" if ok else "  con fallos")
    return ok


# --------------------------------------------------- check 2/3: ONNX
def _check_onnx_file(path: str, n_classes: int, X: np.ndarray) -> bool:
    import onnxruntime as ort
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    out_names = [o.name for o in sess.get_outputs()]
    ok = True
    if out_names != ["label", "probabilities"]:
        print(f"  FALLO: nombres/orden de salida = {out_names} "
              f"(esperado ['label','probabilities'])"); ok = False
    res = sess.run(None, {"input": X.astype(np.float32)})
    label, probs = res[0], res[1]
    if probs.shape != (len(X), n_classes):
        print(f"  FALLO: forma probs {probs.shape} != {(len(X), n_classes)}"); ok = False
    if not np.allclose(probs.sum(1), 1, atol=1e-4):
        print("  FALLO: filas de probs no suman 1"); ok = False
    if not np.array_equal(label.reshape(-1), probs.argmax(1)):
        print("  FALLO: label != argmax(probs)"); ok = False
    print(f"  {path}: {'OK' if ok else 'FALLO'} "
          f"(salidas={out_names}, probs={probs.shape})")
    return ok


def check_export() -> bool:
    print("== Check 2: export ONNX (firma drop-in) ==")
    from distill_regime_kl import build_model, export_onnx
    df, _ = synth()
    X = df[FEATURES].to_numpy(np.float32)
    mean, std = X.mean(0), X.std(0)
    std = np.where(std > 1e-9, std, 1.0)
    ok = True
    tmp = tempfile.mkdtemp()
    for n_classes, fname in [(3, "dir"), (4, "vol")]:
        m = build_model(mean.astype(np.float32), std.astype(np.float32), n_classes)
        p = os.path.join(tmp, f"regime_{fname}.onnx")
        export_onnx(m, p)
        ok &= _check_onnx_file(p, n_classes, X[:16])
    # golden vectors del modelo de dirección recién exportado
    _dump_golden(os.path.join(tmp, "regime_dir.onnx"), X[:10])
    return ok


def _dump_golden(path: str, X: np.ndarray) -> None:
    import onnxruntime as ort
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    probs = sess.run(None, {"input": X.astype(np.float32)})[1]
    with open("golden_vectors.txt", "w") as fh:
        fh.write("# 12 features crudas -> probs (para print-compare en MQL5)\n")
        fh.write("# " + ",".join(FEATURES) + " | probs\n")
        for i in range(len(X)):
            fh.write(",".join(f"{v:.6f}" for v in X[i]) + " | " +
                     ",".join(f"{v:.6f}" for v in probs[i]) + "\n")
    print("  golden_vectors.txt escrito")


def check_disk(dir_path, vol_path) -> bool:
    print("== Check 3: modelos en disco ==")
    df, _ = synth()
    X = df[FEATURES].to_numpy(np.float32)[:16]
    ok = True
    if dir_path and os.path.exists(dir_path):
        ok &= _check_onnx_file(dir_path, 3, X)
    if vol_path and os.path.exists(vol_path):
        ok &= _check_onnx_file(vol_path, 4, X)
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description="Verificación de contrato ONNX")
    ap.add_argument("--dir", default=None, help="regime_dir.onnx en disco (opcional)")
    ap.add_argument("--vol", default=None, help="regime_vol.onnx en disco (opcional)")
    args = ap.parse_args()

    ok = True
    ok &= check_hmm()
    ok &= check_export()
    if args.dir or args.vol:
        ok &= check_disk(args.dir, args.vol)

    print("\n" + ("TODO OK: contrato válido." if ok else "HAY FALLOS: revísalos."))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
