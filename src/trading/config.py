"""Carga y valida config/settings.yaml y config/exclusions.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

from trading.paths import CONFIG_DIR


class Capital(BaseModel):
    live_eur: float
    paper_eur: float
    start_date: str | None = None


class Exchange(BaseModel):
    name: str = "bitvavo"
    quote: str = "EUR"
    maker_fee: float
    taker_fee: float
    entry_limit_buffer: float
    stop_limit_offset: float


class Universe(BaseModel):
    min_history_days: int
    min_binance_quote_volume_usd: float
    min_bitvavo_volume_eur: float
    max_bitvavo_spread_bps: float
    max_fdv_mc_ratio: float
    max_price_mismatch: float


class Weights(BaseModel):
    trend: float
    relative: float
    participation: float
    entry_quality: float


class Signals(BaseModel):
    shortlist_size: int
    weights: Weights
    max_overextension_atr: float
    max_rsi: float
    max_correlation: float


class Trade(BaseModel):
    atr_period: int
    stop_atr: float
    take_profit_atr: float | None     # None = sin objetivo (p. ej. V2C)
    max_holding_days: int
    reentry_cooldown_days: int


class Risk(BaseModel):
    risk_per_trade: float
    max_positions: int
    max_open_risk: float
    max_position_weight: float
    max_drawdown_pause: float
    drawdown_pause_days: int = 14


class Regime(BaseModel):
    breadth_threshold: float
    multipliers: dict[str, float]


class Costs(BaseModel):
    slippage_high_liquidity: float
    slippage_mid_liquidity: float
    slippage_low_liquidity: float
    stop_extra_slippage: float


class Schedule(BaseModel):
    timezone: str
    main_run: str
    monitor_runs: list[str]


class Research(BaseModel):
    max_web_searches: int
    max_posts_per_coin: int
    rss_feeds: list[str]
    fred_series: list[str]


class Mode(BaseModel):
    live_enabled: bool = True


class Settings(BaseModel):
    capital: Capital
    exchange: Exchange
    universe: Universe
    signals: Signals
    trade: Trade
    risk: Risk
    regime: Regime
    costs: Costs
    schedule: Schedule
    research: Research
    mode: Mode


class Exclusions(BaseModel):
    stablecoins: list[str]
    wrapped: list[str]
    commodities: list[str]
    leveraged_suffixes: list[str]
    leveraged_bases: list[str]
    manual: list[str] = []

    def excluded_assets(self) -> set[str]:
        return {a.upper() for a in self.stablecoins + self.wrapped + self.commodities + self.manual}

    def is_leveraged(self, asset: str) -> bool:
        asset = asset.upper()
        for suffix in self.leveraged_suffixes:
            if asset.endswith(suffix) and asset[: -len(suffix)] in self.leveraged_bases:
                return True
        return False

    def is_excluded(self, asset: str) -> bool:
        return asset.upper() in self.excluded_assets() or self.is_leveraged(asset)


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def load_settings(path: str | None = None) -> Settings:
    return Settings.model_validate(_load_yaml(Path(path) if path else CONFIG_DIR / "settings.yaml"))


@lru_cache(maxsize=1)
def load_exclusions(path: str | None = None) -> Exclusions:
    return Exclusions.model_validate(_load_yaml(Path(path) if path else CONFIG_DIR / "exclusions.yaml"))
