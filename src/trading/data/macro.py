"""Macro (FRED en CSV, sin clave) y Fear & Greed (alternative.me)."""

from __future__ import annotations

import io

import pandas as pd

from trading.net import HttpClient

FRED_NAMES = {
    "DFF": "Tipo efectivo de fondos federales (%)",
    "DGS10": "Bono EE. UU. 10 años (%)",
    "DTWEXBGS": "Índice amplio del dólar",
    "VIXCLS": "VIX (volatilidad S&P 500)",
}


def fred_latest(series: list[str]) -> list[dict]:
    http = HttpClient("https://fred.stlouisfed.org", rate=2.0)
    out = []
    for sid in series:
        try:
            text = http.get(f"/graph/fredgraph.csv", {"id": sid}).text
            df = pd.read_csv(io.StringIO(text))
            df.columns = ["date", "value"]
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df = df.dropna()
            last = df.iloc[-1]
            prev = df[pd.to_datetime(df["date"]) <= pd.to_datetime(last["date"]) - pd.Timedelta(days=30)]
            change = float(last["value"] - prev.iloc[-1]["value"]) if not prev.empty else None
            out.append({"series": sid, "name": FRED_NAMES.get(sid, sid), "date": str(last["date"]),
                        "value": float(last["value"]), "change_30d": change})
        except Exception as exc:  # macro es contexto, no debe tumbar el escaneo
            out.append({"series": sid, "name": FRED_NAMES.get(sid, sid), "error": str(exc)[:120]})
    return out


def fear_greed(days: int = 30) -> dict:
    http = HttpClient("https://api.alternative.me", rate=2.0)
    data = http.get_json("/fng/", {"limit": days})["data"]
    values = [int(d["value"]) for d in data]
    return {"value": values[0], "label": data[0]["value_classification"],
            "avg_7d": round(sum(values[:7]) / min(7, len(values)), 1),
            "avg_30d": round(sum(values) / len(values), 1)}
