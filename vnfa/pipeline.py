# -*- coding: utf-8 -*-
"""Logic dùng chung cho chạy hằng ngày và backfill: ghi snapshot rồi so sánh với kỳ trước."""
from __future__ import annotations

import pandas as pd

from .diff import CHANGE_COLUMNS, diff_holdings


def ingest_fund(store, meta: dict, holdings: pd.DataFrame, observed_date: str, cfg: dict, asset_class: str = "STOCK"):
    """Trả về (status, changes). status ∈ {'baseline','changed','unchanged','empty'}."""
    status, prev = store.append_if_changed(meta["code"], holdings, observed_date)
    if status != "changed" or prev is None:
        return status, pd.DataFrame(columns=CHANGE_COLUMNS)
    th = cfg["thresholds"]
    ch = diff_holdings(prev, holdings, min_vol_pct=th["min_volume_change_pct"] / 100.0,
                       min_w_delta=th["min_weight_delta_pp"] / 100.0)
    if ch.empty:
        return status, pd.DataFrame(columns=CHANGE_COLUMNS)
    ch.insert(0, "observed_date", observed_date)
    ch.insert(1, "fund", meta["code"])
    ch.insert(2, "mgmt_co", meta.get("mgmt_co"))
    ch["asset_class"] = asset_class
    return status, ch.reindex(columns=CHANGE_COLUMNS)


def recent_changes(store, observed_date: str, days: int) -> pd.DataFrame:
    """Các thay đổi đã ghi trong `days` ngày trước observed_date (không gồm chính ngày đó)."""
    old = store.read_changes()
    if old.empty:
        return old
    d = pd.to_datetime(old.observed_date)
    t = pd.Timestamp(observed_date)
    return old[(d < t) & (d >= t - pd.Timedelta(days=days))]
