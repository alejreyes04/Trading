"""
distill_regime_kl.py — RegimeScanner v2.1 (destilación KL soft-labels)
=====================================================================
SPDX-License-Identifier: MIT

Entrena DOS clasificadores *student* (uno por eje) DESTILANDO los posteriores
suaves del HMM (teacher, ver label_regime_hmm.py) con divergencia KL, y los
exporta a ONNX como REEMPLAZO *drop-in* de los modelos que produce
train_regime_onnx.py. RegimeScanner.mq5 NO necesita recompilarse.

Contrato ONNX que impone el indicador (RegimeScanner.mq5, LoadAxisModel/PredictX)
-------------------------------------------------------------------------------
  * entrada  : float32 [N, 12]  (batch dinámico)  — FEATURES CRUDAS.
               => el StandardScaler va HORNEADO dentro del grafo (capa fija).
                  El indicador alimenta f[] sin escalar; escalar aquí evita el
                  doble-escalado silencioso.
  * salida 0 : label          int64   [N]
  * salida 1 : probabilities  float32 [N, n_clases]
               El indicador lee sólo 'probabilities', pero OnnxRun(...,lbl,prb)
               EXIGE que ambas salidas existan y en ese orden (label primero).
  * n_clases : 3 (dir: UP,DOWN,NONE) / 4 (vol: QUIET,VOLATILE,EXPANSION,CRISIS).

Pérdida de destilación
----------------------
  loss = alpha * KL(log_softmax(logits/T) || gamma_HMM) * T^2
       + (1-alpha) * CE(logits, y_forward)

  - El término KL contra el posterior suave del HMM es lo que SUAVIZA las
    fronteras ambiguas (el objetivo real del usuario).
  - El término CE contra la etiqueta forward-looking ancla el significado
    económico del eje. alpha=1.0 => destilación pura HMM.

Instalación:
    pip install -r requirements.txt      # torch, onnx, hmmlearn, ...

Uso:
    python label_regime_hmm.py regime_features.csv --out regime_labeled.csv
    python distill_regime_kl.py regime_labeled.csv
    # -> regime_dir.onnx, regime_vol.onnx  (cópialos a MQL5\\Files\\)
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

# Reutiliza el etiquetado forward y la composición del script existente
# (paridad exacta de definición con la ruta sklearn).
from train_regime_onnx import (make_axis_labels, compose, H, EMBARGO,
                               TEST_FRAC, FEATURES, DIR_NAMES, VOL_NAMES,
                               REG_NAMES)

SEED = 42
VAL_FRAC = 0.15           # slice de validación (final del train) para early stop
EPOCHS = 400
PATIENCE = 30
LR = 1e-3
TEMP = 2.0                # temperatura de destilación
ALPHA = 0.7               # peso del término KL (soft) vs CE (hard forward)

DIR_GAMMA = ["pdir_up", "pdir_down", "pdir_none"]
VOL_GAMMA = ["pvol_quiet", "pvol_vol", "pvol_exp", "pvol_crisis"]


# --------------------------------------------------------------- modelo
def build_model(mean, std, n_classes):
    import torch
    import torch.nn as nn

    class DistilledAxis(nn.Module):
        """MLP (32,16) con StandardScaler HORNEADO. forward -> (label, probs)
        replicando la firma de salida de skl2onnx (label int64 + probs f32)."""

        def __init__(self):
            super().__init__()
            self.register_buffer("mean", torch.tensor(mean, dtype=torch.float32))
            self.register_buffer("std", torch.tensor(std, dtype=torch.float32))
            self.net = nn.Sequential(
                nn.Linear(len(FEATURES), 32), nn.ReLU(),
                nn.Linear(32, 16), nn.ReLU(),
                nn.Linear(16, n_classes))

        def logits(self, x):
            z = (x - self.mean) / self.std
            return self.net(z)

        def forward(self, x):
            logits = self.logits(x)
            probs = torch.softmax(logits, dim=1)
            label = torch.argmax(probs, dim=1).to(torch.int64)
            return label, probs

    return DistilledAxis()


def _class_weights(y, n_classes):
    import torch
    counts = np.bincount(y, minlength=n_classes).astype(float)
    counts = np.where(counts > 0, counts, 1.0)
    w = counts.sum() / (n_classes * counts)
    return torch.tensor(w / w.mean(), dtype=torch.float32)


def train_axis(name, X, gamma, yfwd, n_classes, out_path):
    import torch
    import torch.nn.functional as F

    torch.manual_seed(SEED)
    np.random.seed(SEED)

    n = len(X)
    n_test = int(n * TEST_FRAC)
    n_tr_full = n - n_test - EMBARGO
    n_val = int(n_tr_full * VAL_FRAC)
    n_tr = n_tr_full - n_val

    sl_tr = slice(0, n_tr)
    sl_val = slice(n_tr, n_tr_full)
    sl_te = slice(n_tr_full + EMBARGO, n)

    # Scaler SOLO con el train (nunca con val/test): se hornea en el modelo.
    mean = X[sl_tr].mean(axis=0)
    std = X[sl_tr].std(axis=0)
    std = np.where(std > 1e-9, std, 1.0)

    model = build_model(mean.astype(np.float32), std.astype(np.float32), n_classes)
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    class_w = _class_weights(yfwd[sl_tr], n_classes)

    def tens(a, dt=torch.float32):
        return torch.tensor(a, dtype=dt)

    Xtr, Gtr, Ytr = tens(X[sl_tr]), tens(gamma[sl_tr]), tens(yfwd[sl_tr], torch.long)
    Xva, Yva = tens(X[sl_val]), tens(yfwd[sl_val], torch.long)

    best_val, best_state, bad = np.inf, None, 0
    for ep in range(EPOCHS):
        model.train()
        opt.zero_grad()
        logits = model.logits(Xtr)
        logT = F.log_softmax(logits / TEMP, dim=1)
        kl = F.kl_div(logT, Gtr, reduction="batchmean") * (TEMP * TEMP)
        ce = F.cross_entropy(logits, Ytr, weight=class_w)
        loss = ALPHA * kl + (1.0 - ALPHA) * ce
        loss.backward()
        opt.step()

        model.eval()
        with torch.no_grad():
            vloss = F.cross_entropy(model.logits(Xva), Yva).item()
        if vloss < best_val - 1e-4:
            best_val, best_state, bad = vloss, {k: v.clone() for k, v in
                                                model.state_dict().items()}, 0
        else:
            bad += 1
            if bad >= PATIENCE:
                break
    if best_state is not None:
        model.load_state_dict(best_state)

    # -------- evaluación en test cronológico --------
    from sklearn.metrics import classification_report, balanced_accuracy_score
    names = DIR_NAMES if n_classes == 3 else VOL_NAMES
    model.eval()
    with torch.no_grad():
        _, prob_te = model(tens(X[sl_te]))
    pred_te = prob_te.numpy().argmax(1)
    yte = yfwd[sl_te]
    print(f"\n========== EJE {name} (test cronológico) ==========")
    print(classification_report(yte, pred_te, labels=list(range(n_classes)),
                                target_names=names, zero_division=0))
    print(f"Balanced accuracy: {balanced_accuracy_score(yte, pred_te):.3f}")

    export_onnx(model, out_path)
    return pred_te, yte, sl_te


def export_onnx(model, out_path):
    import warnings

    import torch
    model.eval()
    dummy = torch.zeros(1, len(FEATURES), dtype=torch.float32)
    # Exportador TorchScript legado (dynamo=False): reproduce con control exacto
    # la firma [label(int64), probabilities(float32)] que espera OnnxRun en MQL5,
    # sin depender de onnxscript. Silenciamos su aviso de deprecación.
    warnings.filterwarnings("ignore", category=DeprecationWarning,
                            module="torch.onnx")
    torch.onnx.export(
        model, dummy, out_path,
        opset_version=15,
        input_names=["input"],
        output_names=["label", "probabilities"],
        dynamic_axes={"input": {0: "N"},
                      "label": {0: "N"},
                      "probabilities": {0: "N"}},
        dynamo=False,      # exportador TorchScript legado: no requiere onnxscript
    )
    print(f"Modelo ONNX exportado: {out_path}")


# ------------------------------------------------------------------ main
def main() -> None:
    ap = argparse.ArgumentParser(description="Destilación KL HMM->ONNX (v2.1)")
    ap.add_argument("csv", nargs="?", default="regime_labeled.csv",
                    help="CSV aumentado de label_regime_hmm.py")
    ap.add_argument("--dir-out", default="regime_dir.onnx")
    ap.add_argument("--vol-out", default="regime_vol.onnx")
    args = ap.parse_args()

    df = pd.read_csv(args.csv).dropna().reset_index(drop=True)
    for col in FEATURES + DIR_GAMMA + VOL_GAMMA + ["close"]:
        if col not in df.columns:
            print(f"ERROR: falta la columna '{col}'. ¿Corriste label_regime_hmm.py?")
            sys.exit(1)

    # Etiquetas forward (ancla CE) — misma DEFINICIÓN que la ruta sklearn.
    ydir, yvol = make_axis_labels(df["close"].to_numpy(float))
    df["ydir_fwd"], df["yvol_fwd"] = ydir, yvol
    df = df[(df["ydir_fwd"] >= 0) & (df["yvol_fwd"] >= 0)].reset_index(drop=True)

    X = df[FEATURES].to_numpy(np.float32)
    gdir = df[DIR_GAMMA].to_numpy(np.float32)
    gvol = df[VOL_GAMMA].to_numpy(np.float32)
    yd = df["ydir_fwd"].to_numpy(np.int64)
    yv = df["yvol_fwd"].to_numpy(np.int64)

    print(f"Filas: {len(df)} | T={TEMP} alpha={ALPHA} | embargo={EMBARGO}")

    pdd, ydd, sl = train_axis("DIRECCIÓN", X, gdir, yd, 3, args.dir_out)
    pvv, yvv, _ = train_axis("VOLATILIDAD", X, gvol, yv, 4, args.vol_out)

    # -------- informe compuesto de 8 regímenes --------
    from sklearn.metrics import classification_report, confusion_matrix
    true8 = compose(ydd, yvv)
    pred8 = compose(pdd, pvv)
    print("\n========== 8 REGÍMENES COMPUESTOS (test) ==========")
    print(classification_report(true8, pred8, labels=list(range(8)),
                                target_names=REG_NAMES, zero_division=0))
    print("Matriz de confusión (filas=real, cols=pred):")
    print(confusion_matrix(true8, pred8, labels=list(range(8))))

    print("\nModelos guardados:", args.dir_out, args.vol_out)
    print(r"Cópialos a  <Carpeta de datos MT5>\MQL5\Files\  y activa "
          r"InpUseONNX=true.")
    print("REGLA DE DESPLIEGUE: cada eje debe superar su baseline de clase "
          "mayoritaria en balanced accuracy Y el gating debe mejorar la EA en "
          "walk-forward. Si sólo empata con el ensemble, NO despliegues: añade "
          "opacidad sin edge. Verifica el contrato ONNX con verify_onnx_contract.py.")


if __name__ == "__main__":
    main()
