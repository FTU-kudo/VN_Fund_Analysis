#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Xuất data/holdings/*.csv sang bảng dài tương thích schema của smart_money_quant.py.

LƯU Ý PHƯƠNG PHÁP LUẬN:
- `publish_date` trong bảng xuất này được lấy từ `observed_date` (ngày hệ thống lần đầu thấy
  snapshot trên Fmarket), KHÔNG phải ngày công bố chính thức của SSC/công ty quản lý quỹ.
  Cột `publish_date_inferred=True` và `publish_date_basis` được xuất rõ để người đọc không nhầm lẫn.
- `period_end` = cuối tháng trước `update_date` (SUY RA, không có trực tiếp trong nguồn).
- `complete_top10` chỉ bằng `True` khi khối snapshot `(fund, observed_date, content_hash)` thực sự có >= 10 mã.

Kết quả: exports/holdings_long.csv (sep=';')
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vnfa.store import SEP, Store, atomic_write_csv, read_csv_safe  # noqa: E402


def export_long(data_dir: str | Path, out_path: str | Path) -> pd.DataFrame:
    data = Path(data_dir)
    store = Store(data, write=False)
    names = store.fund_names()
    parts = [read_csv_safe(p, sep=SEP, dtype=str) for p in sorted((data / "holdings").glob("*.csv"))]
    if not parts:
        print("Chưa có dữ liệu trong data/holdings/")
        return pd.DataFrame()
    h = pd.concat(parts, ignore_index=True)
    h["weight"] = pd.to_numeric(h.weight, errors="raise")
    # Mỗi (quỹ, observed_date, content_hash) là một khối snapshot quan sát được
    block_sizes = h.groupby(["fund", "observed_date", "content_hash"], dropna=False)["ticker"].transform("count")
    h["publish_date"] = pd.to_datetime(h.observed_date, errors="coerce")
    upd = pd.to_datetime(h.update_date, errors="coerce")
    h["period_end"] = upd.dt.to_period("M").dt.start_time - pd.Timedelta(days=1)
    h["mgmt_co"] = h.fund.map(names)
    out = h[["fund", "mgmt_co", "ticker", "weight", "period_end", "publish_date", "rank", "update_date"]].copy()
    out["complete_top10"] = (block_sizes >= 10).to_numpy()
    out["period_end_inferred"] = True
    out["publish_date_inferred"] = True
    out["publish_date_basis"] = "observed_date_proxy_not_official_publication"
    atomic_write_csv(out, out_path, sep=SEP)
    print(f"Đã xuất {len(out)} dòng, {out.fund.nunique()} quỹ -> {out_path}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--out", default=str(ROOT / "exports" / "holdings_long.csv"))
    a = ap.parse_args()
    export_long(a.data_dir, a.out)


if __name__ == "__main__":
    main()
