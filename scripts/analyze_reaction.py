#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mô tả NAV, cơ cấu tài sản và thay đổi vị thế ước tính quanh một mốc thị trường.

Đọc data/ do run_daily.py tạo và lưu reports/reaction_<ngày>.md.
Các chỉ số là mô tả từ dữ liệu công bố; không xác nhận ngày giao dịch và không có benchmark.
`nav.csv` (ngày hiệu lực NAV từ API lịch sử) và `nav_snapshots.csv` (ngày hệ thống quan sát)
được giữ riêng, KHÔNG tự động nối qua khoảng trống lịch sử để tránh bóp méo lợi nhuận và MDD.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vnfa.store import SEP, read_csv_safe  # noqa: E402

TYPE_NAME = {"STOCK": "Quỹ cổ phiếu", "BALANCED": "Quỹ cân bằng", "BOND": "Quỹ trái phiếu/MMF"}


def md_table(df: pd.DataFrame, fmt: dict | None = None, na_label: str = "NA") -> str:
    fmt = fmt or {}
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        out.append(
            "| "
            + " | ".join(fmt.get(c, lambda x: str(x))(r[c]) if pd.notna(r[c]) else na_label for c in cols)
            + " |"
        )
    return "\n".join(out)


def pct(x): return f"{x * 100:+.1f}%"
def pct0(x): return f"{x * 100:.1f}%"


def _clean_nav_series(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or not {"fund", "date", "nav"}.issubset(df.columns):
        return pd.DataFrame(columns=["fund", "date", "nav"])
    out = df[["fund", "date", "nav"]].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    out["nav"] = pd.to_numeric(out["nav"], errors="coerce")
    valid = out["fund"].notna() & out["date"].notna() & np.isfinite(out["nav"]) & (out["nav"] > 0)
    out = out[valid].copy()
    return out.drop_duplicates(["fund", "date"], keep="last").sort_values(["fund", "date"], kind="stable").reset_index(drop=True)


def load_with_snapshots(data: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load separated tables without stitching `nav.csv` and `nav_snapshots.csv`."""
    def rd(name: str, **kwargs) -> pd.DataFrame:
        p = data / name
        return read_csv_safe(p, sep=SEP, **kwargs) if p.exists() else pd.DataFrame()

    funds = rd("funds.csv", dtype=str)
    nav = _clean_nav_series(rd("nav.csv", dtype={"fund": str, "date": str}))
    snap = _clean_nav_series(rd("nav_snapshots.csv", dtype={"fund": str, "date": str}))
    alloc = rd("allocation.csv", dtype={"fund": str})
    ch = rd("changes.csv", dtype={"observed_date": str})
    ind = rd("industry.csv", dtype={"fund": str})
    return funds, nav, snap, alloc, ch, ind


def load(data: Path):
    """Backward-compatible 5-tuple loader returning official `nav.csv` without snapshot stitching."""
    funds, nav, _snap, alloc, ch, ind = load_with_snapshots(data)
    return funds, nav, alloc, ch, ind


def nav_since(
    nav: pd.DataFrame,
    funds: pd.DataFrame,
    peak: str,
    fresh_days: int,
    base_tol: int = 14,
    max_gap_days: int = 35,
) -> tuple[pd.DataFrame, str, int]:
    """Hiệu suất từ mốc: điểm NAV gần mốc nhất (≤ mốc, hoặc sau mốc tối đa base_tol ngày) đến NAV mới nhất.

    Các điểm NAV không dương/hữu hạn hoặc không có ngày hợp lệ bị loại, không được diễn giải thành 0.
    Chuỗi có khoảng trống nội bộ lớn hơn `max_gap_days` bị loại để không báo cáo MDD giả tạo qua lỗ hổng dữ liệu.
    """
    nav = nav.copy()
    nav["date"] = pd.to_datetime(nav.date, errors="coerce")
    nav["nav"] = pd.to_numeric(nav.nav, errors="coerce")
    valid = nav.date.notna() & np.isfinite(nav.nav) & (nav.nav > 0)
    nav = nav[valid].copy()
    if nav.empty:
        return pd.DataFrame(columns=["fund", "ret", "mdd", "from", "to"]), "", 0
    nav = nav.drop_duplicates(["fund", "date"], keep="last")
    end, pk = nav.date.max(), pd.Timestamp(peak)
    rows = []
    skipped_gap = 0
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
        max_gap = int(seg.date.diff().dt.days.max())
        if max_gap > max_gap_days:
            skipped_gap += 1
            continue
        rows.append((f, seg.nav.iloc[-1] / seg.nav.iloc[0] - 1, (seg.nav / seg.nav.cummax() - 1).min(),
                     start.date().isoformat(), seg.date.iloc[-1].date().isoformat()))
    df = pd.DataFrame(rows, columns=["fund", "ret", "mdd", "from", "to"])
    if {"code", "asset_code", "mgmt_co"}.issubset(funds.columns):
        df = df.merge(funds[["code", "asset_code", "mgmt_co"]], left_on="fund", right_on="code", how="left")
    else:
        df["code"], df["asset_code"], df["mgmt_co"] = np.nan, np.nan, np.nan
    return df, end.date().isoformat(), skipped_gap


def _render_nav_section(
    L: list[str],
    heading: str,
    series_note: str,
    nav: pd.DataFrame,
    funds: pd.DataFrame,
    peak: str,
    fresh_days: int,
    max_gap_days: int,
) -> None:
    if nav.empty:
        return
    perf, last, skipped_gap = nav_since(nav, funds, peak, fresh_days, max_gap_days=max_gap_days)
    first = pd.to_datetime(nav.date, errors="coerce").min().date().isoformat()
    n_all = nav.fund.nunique()
    L += [heading, "", series_note, ""]
    if perf.empty:
        gap_note = f" ({skipped_gap} quỹ bị loại do khoảng trống chuỗi > {max_gap_days} ngày)" if skipped_gap else ""
        L += [
            f"Không đủ chuỗi NAV liên tục quanh mốc {peak}: phạm vi hiện có là `{first}` → `{last}` ({n_all} quỹ){gap_note}. "
            "Không ghép tự động `nav.csv` với `nav_snapshots.csv` để tránh sai lệch định nghĩa ngày và MDD.",
            "",
        ]
        return
    gap_suffix = f" ({skipped_gap} quỹ bị loại do khoảng trống > {max_gap_days} ngày)." if skipped_gap else "."
    L += [
        f"{len(perf)}/{n_all} quỹ đủ điều kiện (NAV mới trong {fresh_days} ngày so với `{last}`, có điểm gốc quanh mốc, "
        f"không có khoảng trống > {max_gap_days} ngày){gap_suffix} "
        f"Điểm gốc từng quỹ nằm trong khoảng `{perf['from'].min()}` → `{perf['from'].max()}`.",
        "",
    ]
    g = perf.groupby("asset_code").agg(
        n=("fund", "count"),
        median=("ret", "median"),
        p25=("ret", lambda s: s.quantile(.25)),
        p75=("ret", lambda s: s.quantile(.75)),
        mdd_median=("mdd", "median"),
    ).reset_index()
    g["asset_code"] = g.asset_code.map(TYPE_NAME).fillna(g.asset_code)
    g.columns = ["Loại quỹ", "Số quỹ", "Trung vị", "P25", "P75", "MDD trung vị"]
    L += [md_table(g, {"Trung vị": pct, "P25": pct, "P75": pct, "MDD trung vị": pct}), ""]
    eq = perf[perf.asset_code == "STOCK"].sort_values("ret")
    if len(eq) >= 6:
        tb = pd.concat([eq.head(3), eq.tail(3)])[["fund", "mgmt_co", "ret", "mdd"]]
        tb.columns = ["Quỹ cổ phiếu (3 yếu nhất, 3 mạnh nhất)", "CTQL", "Hiệu suất", "MDD"]
        L += [md_table(tb, {"Hiệu suất": pct, "MDD": pct}), ""]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--peak", default="2026-05-07", help="mốc tham chiếu (đỉnh VN-Index đóng cửa 1.909 điểm)")
    ap.add_argument("--fresh-days", type=int, default=14, help="chỉ dùng quỹ có NAV mới trong N ngày so với ngày NAV mới nhất")
    ap.add_argument("--max-gap-days", type=int, default=35, help="loại chuỗi có khoảng trống nội bộ vượt quá N ngày")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    data = Path(a.data_dir)
    funds, nav, snap, alloc, ch, ind = load_with_snapshots(data)
    L = [f"# Phản ứng của các quỹ kể từ {a.peak}", ""]

    # 1A) Chuỗi NAV lịch sử chính thức (nav.csv — navDate)
    _render_nav_section(
        L,
        f"## 1A. Chuỗi NAV lịch sử (`nav.csv`) từ {a.peak}",
        "_Ý nghĩa ngày:_ `date` là `navDate` từ endpoint lịch sử `/product/get-nav-history` (thời điểm công bố gốc không có).",
        nav, funds, a.peak, a.fresh_days, a.max_gap_days,
    )

    # 1B) Chuỗi NAV chụp hằng ngày (nav_snapshots.csv — observed_date, tách biệt hoàn toàn với nav.csv)
    _render_nav_section(
        L,
        f"## 1B. Chuỗi NAV quan sát hằng ngày (`nav_snapshots.csv`) từ {a.peak}",
        "_Ý nghĩa ngày:_ `date` là ngày hệ thống quan sát (`observed_date`), KHÔNG xác nhận ngày hiệu lực chính thức của NAV.",
        snap, funds, a.peak, a.fresh_days, a.max_gap_days,
    )

    # 2) Cơ cấu tài sản
    if not alloc.empty:
        al = alloc.assign(pct=pd.to_numeric(alloc.pct, errors="coerce"), report_date=pd.to_datetime(alloc.report_date, errors="coerce"))
        al = al[al.report_date.notna()].drop_duplicates(["fund", "report_date", "asset_code"], keep="last")
        # Thiếu một asset_code không được hiểu là tỷ trọng 0: nguồn có thể chưa công bố đủ cơ cấu.
        piv = al.pivot_table(index=["fund", "report_date"], columns="asset_code", values="pct", aggfunc="last").reset_index()
        if {"code", "asset_code"}.issubset(funds.columns):
            piv = piv.merge(funds[["code", "asset_code"]].rename(columns={"asset_code": "type"}),
                            left_on="fund", right_on="code", how="left")
        else:
            piv["code"], piv["type"] = np.nan, np.nan
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
                             stock0=first.get("STOCK", np.nan), stock1=lastr.get("STOCK", np.nan),
                             cash0=first.get("CASH", np.nan), cash1=lastr.get("CASH", np.nan)))
        r = pd.DataFrame(rows)
        if not r.empty:
            r["d_stock"], r["d_cash"] = r.stock1 - r.stock0, r.cash1 - r.cash0
            L += ["## 2. Cơ cấu tài sản: kỳ báo cáo gần mốc → kỳ mới nhất", "",
                  "Trung vị thay đổi tỷ trọng (điểm %), và số quỹ tăng/giảm tiền mặt từ 1 điểm % trở lên.", ""]
            s = r[r.type.isin(["STOCK", "BALANCED"])].groupby("type").agg(
                n_stock=("d_stock", "count"), n_cash=("d_cash", "count"),
                stock0=("stock0", "median"), stock1=("stock1", "median"), d_stock=("d_stock", "median"),
                cash0=("cash0", "median"), cash1=("cash1", "median"), d_cash=("d_cash", "median"),
                up=("d_cash", lambda x: (x >= 0.01).sum()), down=("d_cash", lambda x: (x <= -0.01).sum())).reset_index()
            s["type"] = s.type.map(TYPE_NAME)
            s.columns = ["Loại quỹ", "N Δ CP", "N Δ tiền", "CP đầu", "CP cuối", "Δ CP", "Tiền đầu", "Tiền cuối", "Δ Tiền", "Tăng tiền", "Giảm tiền"]
            L += [md_table(s, {c: pct0 for c in ["CP đầu", "CP cuối", "Tiền đầu", "Tiền cuối"]} | {"Δ CP": pct, "Δ Tiền": pct}, na_label=""), ""]
            top = r[r.type == "STOCK"].sort_values("d_cash")
            if len(top) >= 6:
                tb = pd.concat([top.head(3), top.tail(3)])[["fund", "d0", "d1", "cash0", "cash1", "d_cash"]]
                tb.columns = ["Quỹ cổ phiếu (giảm tiền nhiều nhất, tăng tiền nhiều nhất)", "Từ", "Đến", "Tiền đầu", "Tiền cuối", "Δ Tiền"]
                L += [md_table(tb, {"Tiền đầu": pct0, "Tiền cuối": pct0, "Δ Tiền": pct}, na_label=""), ""]

    # 3) Thay đổi vị thế ước tính
    if not ch.empty:
        asset_cls = ch["asset_class"].fillna("STOCK") if "asset_class" in ch.columns else pd.Series("STOCK", index=ch.index)
        c = ch[(asset_cls == "STOCK") & ch.action.isin(["BUY", "SELL"]) & (ch.basis == "volume")].copy()
        if {"code", "asset_code"}.issubset(funds.columns):
            c = c.merge(funds[["code", "asset_code"]], left_on="fund", right_on="code", how="left")
            c = c[c.asset_code == "STOCK"]
        c["val"] = pd.to_numeric(c.est_value_vnd, errors="coerce") / 1e9
        c["month"] = c.observed_date.astype(str).str[:7]
        if not c.empty:
            L += ["## 3. Thay đổi vị thế ước tính của quỹ cổ phiếu (từ khối lượng)", "",
                  "Giá trị = phần khối lượng còn lại sau proxy quy mô quỹ × giá ngầm định tại kỳ báo cáo; đây không phải dữ liệu lệnh. "
                  "Giá trị thiếu được giữ là `NA`, không ép về `0`. Corporate action chỉ được gắn cờ heuristic, cần đối chiếu nguồn chính thức.", ""]
            m = c.groupby("month").agg(
                n_buy=("action", lambda s: int((s == "BUY").sum())),
                n_sell=("action", lambda s: int((s == "SELL").sum())),
                buy=("val", lambda s: s[s > 0].sum(min_count=1)),
                sell=("val", lambda s: s[s < 0].sum(min_count=1)),
                net=("val", lambda s: s.sum(min_count=1)),
            ).reset_index()
            m["breadth"] = m.n_buy / (m.n_buy + m.n_sell)
            m.columns = ["Tháng", "Tăng vị thế", "Giảm vị thế", "Tăng (tỷ)", "Giảm (tỷ)", "Ròng (tỷ)", "Tỷ lệ tăng"]
            L += [md_table(m, {"Tăng (tỷ)": lambda x: f"{x:,.1f}", "Giảm (tỷ)": lambda x: f"{x:,.1f}",
                               "Ròng (tỷ)": lambda x: f"{x:+,.1f}", "Tỷ lệ tăng": pct0}, na_label="NA"), ""]
            t = c.groupby("ticker").agg(n_funds=("fund", "nunique"), net=("val", lambda s: s.sum(min_count=1))).reset_index()
            t_known = t[t.net.notna()].sort_values("net")
            if not t_known.empty:
                tb = pd.concat([t_known.tail(8).iloc[::-1].assign(k="Tăng vị thế"),
                                t_known.head(8).assign(k="Giảm vị thế")]).drop_duplicates(["k", "ticker"])
                tb = tb[["k", "ticker", "n_funds", "net"]]
                tb.columns = ["Nhóm", "Mã", "Số quỹ", "Ròng (tỷ)"]
                L += ["Mã có thay đổi vị thế ròng ước tính lớn nhất toàn kỳ:", "",
                      md_table(tb, {"Ròng (tỷ)": lambda x: f"{x:+,.1f}"}, na_label="NA"), ""]
    L += ["## Lưu ý", "",
          "- Dựa trên top 10 mỗi quỹ và NAV do Fmarket công bố; quỹ có NAV quan sát quá cũ hoặc chuỗi đứt quãng bị loại khỏi mục 1.",
          "- `nav.csv` (lịch sử `navDate`) và `nav_snapshots.csv` (`observed_date` chụp hằng ngày) được giữ tách biệt, không tự động ghép qua khoảng trống.",
          "- Thiếu asset_code trong cơ cấu không được xem là 0%; chỉ so sánh các tỷ trọng có mặt ở cả hai kỳ.",
          "- Mốc 07/05/2026 là đỉnh VN-Index đóng cửa (1.909 điểm). Đổi bằng `--peak` để phân tích mốc khác.",
          "- Đây là mô tả phản ứng, chưa phải bằng chứng nhân quả hay khuyến nghị đầu tư."]
    md = "\n".join(L) + "\n"
    print(md)
    out = Path(a.out) if a.out else ROOT / "reports" / f"reaction_{a.peak}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")


if __name__ == "__main__":
    main()
