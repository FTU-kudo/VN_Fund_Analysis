#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nhập DANH MỤC ĐẦY ĐỦ của quỹ từ báo cáo/factsheet chính thức của công ty quản lý quỹ (kể cả quỹ không có trên Fmarket, như DCDS).

Fmarket chỉ có top 10; báo cáo tháng/quý của công ty quản lý quỹ có danh mục dài hơn. Tải báo cáo, gõ/trích vào CSV (sep=';')
với cột:  fund;ticker;weight;period_end;source_url    (mgmt_co tùy chọn; weight là phân số 0.0643 hoặc % 6.43)
rồi chạy:  python scripts/import_full_holdings.py duong_dan.csv

Ghi vào data/holdings_full.csv, khóa (fund, period_end, ticker); nhập lại cùng kỳ sẽ ghi đè. source_url bắt buộc để luôn truy được nguồn.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SEP = ";"
REQUIRED = ["fund", "ticker", "weight", "period_end", "source_url"]


def load_clean(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep=SEP, dtype=str)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise SystemExit(f"Thiếu cột: {missing}")
    df["ticker"] = df.ticker.str.strip().str.upper()
    df["fund"] = df.fund.str.strip().str.upper()
    df["weight"] = df.weight.str.replace("%", "", regex=False).str.replace(",", ".", regex=False).astype(float)
    df["period_end"] = pd.to_datetime(df.period_end, dayfirst=False).dt.date.astype(str)
    if df.source_url.isna().any() or (df.source_url.str.strip() == "").any():
        raise SystemExit("source_url bắt buộc cho mọi dòng")
    if df.groupby(["fund", "period_end", "ticker"]).size().max() > 1:
        raise SystemExit("Trùng (fund, period_end, ticker)")
    if df.weight.max() > 1.0:          # nhập theo %
        df["weight"] = df.weight / 100.0
    tot = df.groupby(["fund", "period_end"]).weight.sum()
    if (tot > 1.02).any():
        raise SystemExit(f"Tổng tỷ trọng > 102% ở: {tot[tot > 1.02].to_dict()}")
    return df


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    a = ap.parse_args()
    new = load_clean(a.csv)
    out = Path(a.data_dir) / "holdings_full.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        old = pd.read_csv(out, sep=SEP, dtype={"fund": str, "ticker": str, "period_end": str})
        new = pd.concat([old, new], ignore_index=True).drop_duplicates(["fund", "period_end", "ticker"], keep="last")
    new.sort_values(["fund", "period_end", "weight"], ascending=[True, True, False]).to_csv(out, sep=SEP, index=False)
    print(f"Đã ghi {len(new)} dòng, {new.fund.nunique()} quỹ -> {out}")


if __name__ == "__main__":
    main()
