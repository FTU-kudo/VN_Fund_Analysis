#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Xuất data/holdings/*.csv sang bảng dài đúng schema của smart_money_quant.py.

publish_date = observed_date (mốc đầu tiên hệ thống thấy danh mục => dùng làm Point-In-Time),
period_end   = cuối tháng trước update_date (SUY RA, không có trong nguồn).
Kết quả: exports/holdings_long.csv (sep=';')
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vnfa.store import SEP, Store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--out", default=str(ROOT / "exports" / "holdings_long.csv"))
    a = ap.parse_args()
    data = Path(a.data_dir)
    store = Store(data, write=False)
    names = store.fund_names()
    parts = [pd.read_csv(p, sep=SEP, dtype=str) for p in sorted((data / "holdings").glob("*.csv"))]
    if not parts:
        print("Chưa có dữ liệu trong data/holdings/")
        return
    h = pd.concat(parts, ignore_index=True)
    h["weight"] = h.weight.astype(float)
    # Mỗi (quỹ, observed_date, content_hash) là một kỳ công bố
    h["publish_date"] = pd.to_datetime(h.observed_date)
    upd = pd.to_datetime(h.update_date)
    h["period_end"] = upd.dt.to_period("M").dt.start_time - pd.Timedelta(days=1)
    h["mgmt_co"] = h.fund.map(names)
    out = h.rename(columns={"observed_date": "observed"})[
        ["fund", "mgmt_co", "ticker", "weight", "period_end", "publish_date", "rank", "update_date"]]
    out["complete_top10"] = True
    out["period_end_inferred"] = True
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(a.out, sep=SEP, index=False)
    print(f"Đã xuất {len(out)} dòng, {out.fund.nunique()} quỹ -> {a.out}")


if __name__ == "__main__":
    main()
