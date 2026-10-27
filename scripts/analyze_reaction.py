#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phân tích PHẢN ỨNG của các quỹ trước một mốc thị trường (mặc định: đỉnh VN-Index 07/05/2026).

Đọc data/ do run_daily.py / backfill_hat_ccq.py tạo, in báo cáo Markdown và lưu reports/reaction_<ngày>.md.
Các góc nhìn: (1) hiệu suất NAV từ mốc theo loại quỹ, (2) dịch chuyển cơ cấu tài sản (cổ phiếu/trái phiếu/tiền),
(3) giao dịch chủ động theo ngành, (4) độ rộng mua/bán theo tháng.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vnfa.store import SEP  # noqa: E402

TYPE_NAME = {"STOCK": "Quỹ cổ phiếu", "BALANCED": "Quỹ cân bằng", "BOND": "Quỹ trái phiếu/MMF"}


def md_table(df: pd.DataFrame, fmt: dict | None = None) -> str:
    fmt = fmt or {}
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(fmt.get(c, lambda x: str(x))(r[c]) if pd.notna(r[c]) else "" for c in cols) + " |")
    return "\n".join(out)


def pct(x): return f"{x * 100:+.1f}%"
def pct0(x): return f"{x * 100:.1f}%"


def load(data: Path):
    rd = lambda n, **k: pd.read_csv(data / n, sep=SEP, **k) if (data / n).exists() else pd.DataFrame()
    funds = rd("funds.csv", dtype=str)
    nav = rd("nav.csv", dtype={"fund": str, "date": str})
    alloc = rd("allocation.csv", dtype={"fund": str})
    ch = rd("changes.csv", dtype={"observed_date": str})
    ind = rd("industry.csv", dtype={"fund": str})
    snap = rd("nav_snapshots.csv", dtype={"fund": str, "date": str})
    if not snap.empty:      # chỉ lấy điểm snapshot SAU ngày NAV lịch sử cuối cùng của quỹ (lấp khoảng trống)
        last = nav.groupby("fund").date.max() if not nav.empty else pd.Series(dtype=str)
        snap = snap[snap.date > snap.fund.map(last).fillna("")]
        nav = pd.concat([nav, snap], ignore_index=True)
    return funds, nav, alloc, ch, ind


def nav_since(nav: pd.DataFrame, funds: pd.DataFrame, peak: str, fresh_days: int, base_tol: int = 14) -> tuple[pd.DataFrame, str]:
    """Hiệu suất từ mốc: điểm NAV gần mốc nhất (≤ mốc, hoặc sau mốc tối đa base_tol ngày) đến NAV mới nhất."""
    nav = nav.assign(date=pd.to_datetime(nav.date), nav=nav.nav.astype(float))
    end, pk = nav.date.max(), pd.Timestamp(peak)
    rows = []
    for f, g in nav.groupby("fund"):
        g = g.sort_values("date")
        if g.date.max() < end - pd.Timedelta(days=fresh_days):
            continue
        before = g[g.date <= pk]
        after = g[g.date > pk]
        if not before.empty and (pk - before.date.iloc[-1]).days <= base_tol:
            start = before.date.iloc[-1]
        elif not after.empty and (after.date.iloc[0] - pk).days <= base_tol:
            start = after.date.iloc[0]
        else:
            continue
        seg = g[g.date >= start]
        if len(seg) < 2:
            continue
        rows.append((f, seg.nav.iloc[-1] / seg.nav.iloc[0] - 1, (seg.nav / seg.nav.cummax() - 1).min(),
                     start.date().isoformat(), seg.date.iloc[-1].date().isoformat()))
    df = pd.DataFrame(rows, columns=["fund", "ret", "mdd", "from", "to"])
    return df.merge(funds[["code", "asset_code", "mgmt_co"]], left_on="fund", right_on="code", how="left"), end.date().isoformat()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--peak", default="2026-05-07", help="mốc tham chiếu (đỉnh VN-Index đóng cửa 1.909 điểm)")
    ap.add_argument("--fresh-days", type=int, default=14, help="chỉ dùng quỹ có NAV mới trong N ngày so với ngày NAV mới nhất")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    data = Path(a.data_dir)
    funds, nav, alloc, ch, ind = load(data)
    L = [f"# Phản ứng của các quỹ kể từ {a.peak}", ""]

    # 1) NAV từ mốc
    if not nav.empty:
        perf, last = nav_since(nav, funds, a.peak, a.fresh_days)
        n_all = nav.fund.nunique()
        L += [f"## 1. Hiệu suất NAV từ {a.peak} đến {last}", ""]
        if perf.empty:
            L += [f"Không đủ NAV: dữ liệu NAV mới nhất là {last}, hoặc không quỹ nào có điểm NAV trong 14 ngày quanh mốc {a.peak}. "
                  "Chạy run_daily.py vài ngày (nó tải NAV mới) hoặc đổi --peak.", ""]
        else:
            L += [f"{len(perf)}/{n_all} quỹ được dùng (NAV mới trong {a.fresh_days} ngày so với NAV mới nhất và có điểm NAV gần mốc). "
                  f"Điểm gốc từng quỹ nằm trong khoảng {perf['from'].min()} → {perf['from'].max()}.", ""]
            g = perf.groupby("asset_code").agg(n=("fund", "count"), median=("ret", "median"), p25=("ret", lambda s: s.quantile(.25)),
                                               p75=("ret", lambda s: s.quantile(.75)), mdd_median=("mdd", "median")).reset_index()
            g["asset_code"] = g.asset_code.map(TYPE_NAME).fillna(g.asset_code)
            g.columns = ["Loại quỹ", "Số quỹ", "Trung vị", "P25", "P75", "MDD trung vị"]
            L += [md_table(g, {"Trung vị": pct, "P25": pct, "P75": pct, "MDD trung vị": pct}), ""]
            eq = perf[perf.asset_code == "STOCK"].sort_values("ret")
            if len(eq) >= 6:
                tb = pd.concat([eq.head(3), eq.tail(3)])[["fund", "mgmt_co", "ret", "mdd"]]
                tb.columns = ["Quỹ cổ phiếu (3 yếu nhất, 3 mạnh nhất)", "CTQL", "Hiệu suất", "MDD"]
                L += [md_table(tb, {"Hiệu suất": pct, "MDD": pct}), ""]

    # 2) Cơ cấu tài sản
    if not alloc.empty:
        al = alloc.assign(pct=alloc.pct.astype(float), report_date=pd.to_datetime(alloc.report_date))
        al = al.drop_duplicates(["fund", "report_date", "asset_code"], keep="last")
        piv = al.pivot_table(index=["fund", "report_date"], columns="asset_code", values="pct", aggfunc="last").reset_index().fillna(0)
        piv = piv.merge(funds[["code", "asset_code"]].rename(columns={"asset_code": "type"}), left_on="fund", right_on="code", how="left")
        rows = []
        for f, g in piv.groupby("fund"):
            g = g.sort_values("report_date")
            b = g[g.report_date <= pd.Timestamp(a.peak) + pd.Timedelta(days=31)]
            if b.empty or len(g) < 2:
                continue
            first, lastr = b.iloc[-1], g.iloc[-1]
            if lastr.report_date <= first.report_date:
                continue
            rows.append(dict(fund=f, type=g.type.iloc[0], d0=first.report_date.date().isoformat(), d1=lastr.report_date.date().isoformat(),
                             stock0=first.get("STOCK", 0), stock1=lastr.get("STOCK", 0),
                             cash0=first.get("CASH", 0), cash1=lastr.get("CASH", 0)))
        r = pd.DataFrame(rows)
        if not r.empty:
            r["d_stock"], r["d_cash"] = r.stock1 - r.stock0, r.cash1 - r.cash0
            L += ["## 2. Cơ cấu tài sản: kỳ báo cáo gần mốc → kỳ mới nhất", "",
                  "Trung vị thay đổi tỷ trọng (điểm %), và số quỹ tăng/giảm tiền mặt từ 1 điểm % trở lên.", ""]
            s = r[r.type.isin(["STOCK", "BALANCED"])].groupby("type").agg(
                n=("fund", "count"), stock0=("stock0", "median"), stock1=("stock1", "median"), d_stock=("d_stock", "median"),
                cash0=("cash0", "median"), cash1=("cash1", "median"), d_cash=("d_cash", "median"),
                up=("d_cash", lambda x: (x >= 0.01).sum()), down=("d_cash", lambda x: (x <= -0.01).sum())).reset_index()
            s["type"] = s.type.map(TYPE_NAME)
            s.columns = ["Loại quỹ", "Số quỹ", "CP đầu", "CP cuối", "Δ CP", "Tiền đầu", "Tiền cuối", "Δ Tiền", "Tăng tiền", "Giảm tiền"]
            L += [md_table(s, {c: pct0 for c in ["CP đầu", "CP cuối", "Tiền đầu", "Tiền cuối"]} | {"Δ CP": pct, "Δ Tiền": pct}), ""]
            top = r[r.type == "STOCK"].sort_values("d_cash")
            if len(top) >= 6:
                tb = pd.concat([top.head(3), top.tail(3)])[["fund", "d0", "d1", "cash0", "cash1", "d_cash"]]
                tb.columns = ["Quỹ cổ phiếu (giảm tiền nhiều nhất, tăng tiền nhiều nhất)", "Từ", "Đến", "Tiền đầu", "Tiền cuối", "Δ Tiền"]
                L += [md_table(tb, {"Tiền đầu": pct0, "Tiền cuối": pct0, "Δ Tiền": pct}), ""]

    # 3-4) Giao dịch chủ động
    if not ch.empty:
        c = ch[(ch.asset_class.fillna("STOCK") == "STOCK") & ch.action.isin(["BUY", "SELL"]) & (ch.basis == "volume")].copy()
        c = c.merge(funds[["code", "asset_code"]], left_on="fund", right_on="code", how="left")
        c = c[c.asset_code == "STOCK"]
        c["val"] = c.est_value_vnd.astype(float) / 1e9
        c["month"] = c.observed_date.str[:7]
        if not c.empty:
            L += ["## 3. Giao dịch chủ động của quỹ cổ phiếu (ước tính từ thay đổi khối lượng)", "",
                  "Giá trị = chênh khối lượng chủ động × giá ngầm định tại kỳ báo cáo (tỷ đồng). Đã loại quyền (cổ tức cổ phiếu...) và đổi đồng loạt theo quy mô quỹ.", ""]
            m = c.groupby("month").agg(n_buy=("action", lambda s: (s == "BUY").sum()), n_sell=("action", lambda s: (s == "SELL").sum()),
                                      buy=("val", lambda s: s[s > 0].sum()), sell=("val", lambda s: s[s < 0].sum())).reset_index()
            m["net"] = m.buy + m.sell
            m["breadth"] = m.n_buy / (m.n_buy + m.n_sell)
            m.columns = ["Tháng", "Lệnh mua", "Lệnh bán", "Mua (tỷ)", "Bán (tỷ)", "Ròng (tỷ)", "Tỷ lệ mua"]
            L += [md_table(m, {"Mua (tỷ)": lambda x: f"{x:,.0f}", "Bán (tỷ)": lambda x: f"{x:,.0f}", "Ròng (tỷ)": lambda x: f"{x:+,.0f}",
                               "Tỷ lệ mua": pct0}), ""]
            t = c.groupby("ticker").agg(n_funds=("fund", "nunique"), net=("val", "sum")).reset_index().sort_values("net")
            tb = pd.concat([t.tail(8).iloc[::-1].assign(k="Mua ròng"), t.head(8).assign(k="Bán ròng")])
            tb = tb[["k", "ticker", "n_funds", "net"]]
            tb.columns = ["Nhóm", "Mã", "Số quỹ", "Ròng (tỷ)"]
            L += ["Mã mua/bán ròng nhiều nhất toàn kỳ:", "", md_table(tb, {"Ròng (tỷ)": lambda x: f"{x:+,.0f}"}), ""]
            if not ind.empty:
                pass
    L += ["## Lưu ý", "", "- Dựa trên top 10 mỗi quỹ và NAV do Fmarket công bố; quỹ có NAV cũ bị loại khỏi mục 1.",
          "- Mốc 07/05/2026 là đỉnh VN-Index đóng cửa (1.909 điểm). Đổi bằng `--peak` để phân tích mốc khác.",
          "- Đây là mô tả phản ứng, chưa phải bằng chứng nhân quả hay khuyến nghị đầu tư."]
    md = "\n".join(L) + "\n"
    print(md)
    out = Path(a.out) if a.out else ROOT / "reports" / f"reaction_{a.peak}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")


if __name__ == "__main__":
    main()
