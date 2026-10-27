# -*- coding: utf-8 -*-
"""Đọc config.toml, hợp nhất với giá trị mặc định."""
from __future__ import annotations

import copy
import tomllib
from pathlib import Path

DEFAULTS = {
    "funds": {"asset_codes": ["STOCK", "BALANCED", "BOND"], "exclude": []},
    "collect": {"nav": True, "allocation": True, "industry": True, "bonds": True, "fees": True, "profile": True, "performance": False},
    "thresholds": {"min_volume_change_pct": 5.0, "min_weight_delta_pp": 0.5},
    "corporate_action": {"min_funds": 3, "spread_pct": 1.0, "min_change_pct": 3.0, "lookback_days": 7},
    "watchlist": {"tickers": ["FPT", "HPG", "MBB"]},
    "report": {"top_n": 10},
    "http": {"delay_seconds": 1.0, "timeout_seconds": 30},
}


def load_config(path: str | Path | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if path and Path(path).exists():
        with open(path, "rb") as f:
            user = tomllib.load(f)
        for section, values in user.items():
            if isinstance(values, dict):
                cfg.setdefault(section, {}).update(values)
            else:
                cfg[section] = values
    return cfg
