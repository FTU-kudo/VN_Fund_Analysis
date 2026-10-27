# -*- coding: utf-8 -*-
"""Tạo báo cáo Markdown và bản tóm tắt ngắn (Telegram)."""
from __future__ import annotations

import pandas as pd

from .diff import market_flow

LABEL = {"BUY": "MUA", "SELL": "BÁN", "NEW_IN_TOP10": "Vào top 10", "EXIT_TOP10": "Rời top 10",
         "CORP_ACTION?": "Nghi do quyền", "PRORATA": "Đổi đồng loạt"}
CAVEAT = [
    "Fmarket chỉ công bố **top 10** cổ phiếu mỗi quỹ: \"Vào/Rời top 10\" chưa chắc là mua mới/bán hết.",
    "MUA/BÁN xác định bằng thay đổi **khối lượng** nắm giữ (loại trừ biến động giá). Giá trị là ước tính theo giá tại kỳ báo cáo.",
    "\"Nghi do quyền\": nhiều quỹ cùng đổi khối lượng một mã với tỷ lệ gần như nhau (cổ tức cổ phiếu, thưởng, chia tách); không tính vào dòng tiền ròng.",
    "\"Đổi đồng loạt\": quỹ đổi khối lượng gần như mọi mã cùng tỷ lệ (nhận/chi trả vốn), không phải quan điểm đầu tư; MUA/BÁN chỉ tính phần chênh so với tỷ lệ đó.",
    "Ngày quan sát là ngày hệ thống phát hiện, không phải ngày quỹ giao dịch. Cần đối chiếu factsheet chính thức của công ty quản lý quỹ.",
]


def _pct(x, d=2):
    return "–" if pd.isna(x) else f"{x * 100:.{d}f}%"


def _bn(x):
    return "" if pd.isna(x) else f"{x / 1e9:+.1f}"


def _vol(x):
    return "" if pd.isna(x) else f"{x * 100:+.1f}%"


def summarize(changes: pd.DataFrame, watchlist: list[str], top_n: int = 10) -> dict:
    flow = market_flow(changes)
    if flow.empty:
        return {"flow": flow, "top_buy": flow, "top_sell": flow, "watch": changes.iloc[0:0]}
    top_buy = flow[flow.net_value_bn > 0].sort_values("net_value_bn", ascending=False).head(top_n)
    top_sell = flow[flow.net_value_bn < 0].sort_values("net_value_bn").head(top_n)
    watch = changes[changes.ticker.isin(watchlist)] if not changes.empty else changes
    return {"flow": flow, "top_buy": top_buy, "top_sell": top_sell, "watch": watch}


def _flow_table(df: pd.DataFrame) -> list[str]:
    lines = ["| Mã | Quỹ mua | Quỹ bán | Vào top 10 | Rời top 10 | Ròng (tỷ đồng) |", "|---|---|---|---|---|---|"]
    for r in df.itertuples():
        lines.append(f"| {r.ticker} | {r.n_buy} | {r.n_sell} | {r.n_new} | {r.n_exit} | {r.net_value_bn:+.1f} |")
    return lines


def render_report(date: str, changes: pd.DataFrame, info: dict, cfg: dict) -> str:
    wl = cfg["watchlist"]["tickers"]
    s = summarize(changes, wl, cfg["report"]["top_n"])
    L = [f"# Thay đổi danh mục quỹ mở — {date}", ""]
    L.append(f"- Quỹ kiểm tra: **{info['n_ok']}/{info['n_total']}** (lỗi: {info['n_failed']})")
    L.append(f"- Quỹ có kỳ công bố mới: **{info['n_changed']}** | Ghi mốc lần đầu: {info['n_baseline']}")
    if info.get("failed"):
        L.append(f"- Quỹ lỗi: {', '.join(info['failed'])}")
    L.append("")
    if changes.empty:
        L += ["Không có thay đổi vượt ngưỡng trong lần chạy này.", ""]
    else:
        if not s["top_buy"].empty:
            L += ["## Dòng tiền ròng ước tính: mua nhiều nhất", ""] + _flow_table(s["top_buy"]) + [""]
        if not s["top_sell"].empty:
            L += ["## Dòng tiền ròng ước tính: bán nhiều nhất", ""] + _flow_table(s["top_sell"]) + [""]
        L += [f"## Watchlist ({', '.join(wl)})", ""]
        if s["watch"].empty:
            L += ["Không quỹ nào thay đổi các mã này.", ""]
        else:
            for r in s["watch"].sort_values(["ticker", "fund"]).itertuples():
                L.append(f"- **{r.ticker}** | {r.fund} ({r.mgmt_co}): {LABEL.get(r.action, r.action)}"
                         f" | tỷ trọng {_pct(r.w_prev)} → {_pct(r.w_new)}"
                         + (f" | KL {_vol(r.vol_pct)}" if pd.notna(r.vol_pct) else "")
                         + (f" | ~{_bn(r.est_value_vnd)} tỷ" if pd.notna(r.est_value_vnd) else ""))
            L.append("")
        L += ["## Chi tiết theo quỹ", ""]
        for (fund, co), g in changes.groupby(["fund", "mgmt_co"], dropna=False):
            L += [f"### {fund} ({co})", "", "| Mã | Hành động | Tỷ trọng | Khối lượng | Giá trị ước tính (tỷ) |",
                  "|---|---|---|---|---|"]
            g = g.assign(_k=g.est_value_vnd.abs().fillna(-1)).sort_values("_k", ascending=False)
            for r in g.itertuples():
                L.append(f"| {r.ticker} | {LABEL.get(r.action, r.action)} | {_pct(r.w_prev)} → {_pct(r.w_new)} "
                         f"| {_vol(r.vol_pct)} | {_bn(r.est_value_vnd)} |")
            L.append("")
    L += ["## Lưu ý", ""] + [f"- {c}" for c in CAVEAT]
    return "\n".join(L) + "\n"


def render_text(date: str, changes: pd.DataFrame, info: dict, cfg: dict) -> str:
    s = summarize(changes, cfg["watchlist"]["tickers"], 5)
    t = [f"Quỹ mở {date}: {info['n_changed']} quỹ có kỳ công bố mới ({info['n_ok']}/{info['n_total']} quỹ OK)"]
    if changes.empty:
        return t[0] + "\nKhông có thay đổi vượt ngưỡng."
    if not s["top_buy"].empty:
        t.append("Mua ròng: " + ", ".join(f"{r.ticker} {r.net_value_bn:+.1f}tỷ" for r in s["top_buy"].itertuples()))
    if not s["top_sell"].empty:
        t.append("Bán ròng: " + ", ".join(f"{r.ticker} {r.net_value_bn:+.1f}tỷ" for r in s["top_sell"].itertuples()))
    for r in s["watch"].sort_values(["ticker", "fund"]).itertuples():
        t.append(f"* {r.ticker} {r.fund}: {LABEL.get(r.action, r.action)} {_pct(r.w_prev)}→{_pct(r.w_new)}")
    return "\n".join(t)
