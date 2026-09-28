"""Rutas del repositorio. Todo se resuelve relativo a la raíz del repo."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("TRADING_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = ROOT / "config"
RUNS_DIR = ROOT / "runs"
LEDGER_DIR = ROOT / "ledger"
REPORTS_DIR = ROOT / "reports"
CACHE_DIR = ROOT / "data_cache"
AGENTS_DIR = ROOT / "agents"
