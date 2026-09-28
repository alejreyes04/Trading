"""Histórico diario de Binance desde data.binance.vision (incluye pares ya retirados → menos sesgo de supervivencia).

Se guarda en data_cache/binance_1d/<SIMBOLO>.parquet (no se sube al repo; se regenera con --refresh).
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from trading.config import Exclusions
from trading.data.binance_vision import BinanceVision, klines_to_frame
from trading.net import HttpClient
from trading.paths import CACHE_DIR

log = logging.getLogger(__name__)
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
FILES = "https://data.binance.vision"
PREFIX = "data/spot/monthly/klines/"


def _list(http: HttpClient, prefix: str, pattern: str) -> list[str]:
    out, marker = [], ""
    while True:
        xml = http.get(S3, {"delimiter": "/", "prefix": prefix, "marker": marker}).text
        out += re.findall(pattern, xml)
        if "<IsTruncated>true</IsTruncated>" not in xml:
            return out
        nxt = re.findall(r"<NextMarker>([^<]+)</NextMarker>", xml)
        marker = nxt[0] if nxt else out[-1]


def list_symbols(http: HttpClient, quote: str = "USDT") -> list[str]:
    prefixes = _list(http, PREFIX, r"<Prefix>" + re.escape(PREFIX) + r"([^<]+)/</Prefix>")
    return sorted({p for p in prefixes if p.endswith(quote)})


def list_files(http: HttpClient, symbol: str) -> list[str]:
    return _list(http, f"{PREFIX}{symbol}/1d/", r"<Key>(" + re.escape(PREFIX) + r"[^<]+\.zip)</Key>")


def _download(http: HttpClient, key: str) -> pd.DataFrame | None:
    try:
        content = http.get(f"{FILES}/{key}").content
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            text = z.read(z.namelist()[0]).decode()
    except Exception as exc:
        log.warning("No se pudo descargar %s: %s", key, exc)
        return None
    rows = [line.split(",") for line in text.strip().splitlines()]
    if rows and not rows[0][0].isdigit():   # algunos ficheros traen cabecera
        rows = rows[1:]
    return klines_to_frame(rows) if rows else None


def download(start: str, exclusions: Exclusions, cache: Path = CACHE_DIR / "binance_1d",
             refresh: bool = False, workers: int = 48) -> dict[str, pd.DataFrame]:
    cache.mkdir(parents=True, exist_ok=True)
    http = HttpClient(rate=100.0, timeout=60)
    symbols = [s for s in list_symbols(http) if not exclusions.is_excluded(s[:-4])]
    start_month = pd.Timestamp(start).strftime("%Y-%m")

    def fetch_symbol(sym: str) -> tuple[str, pd.DataFrame | None]:
        path = cache / f"{sym}.parquet"
        if path.exists() and not refresh:
            return sym, pd.read_parquet(path)
        keys = [k for k in list_files(http, sym) if re.search(r"-(\d{4}-\d{2})\.zip$", k).group(1) >= start_month]
        frames = [f for f in (_download(http, k) for k in keys) if f is not None and not f.empty]
        if not frames:
            return sym, None
        df = pd.concat(frames).sort_index()
        df = df[~df.index.duplicated(keep="last")].drop(columns=["close_time_ms"], errors="ignore")
        df.to_parquet(path)
        return sym, df

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = dict(pool.map(fetch_symbol, symbols))
    data = {k: v for k, v in results.items() if v is not None and not v.empty}

    # Mes en curso (el archivo mensual aún no existe): API para los pares que siguen activos.
    bv = BinanceVision()
    last_needed = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=1)
    stale = [s for s, df in data.items() if df.index.max() < last_needed and df.index.max() > last_needed - pd.Timedelta(days=45)]
    recent = bv.klines_many(stale, limit=60)
    for sym, df in recent.items():
        merged = pd.concat([data[sym], df.drop(columns=["close_time_ms"], errors="ignore")]).sort_index()
        data[sym] = merged[~merged.index.duplicated(keep="last")]
    return data
