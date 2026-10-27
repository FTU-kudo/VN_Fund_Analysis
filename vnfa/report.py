# -*- coding: utf-8 -*-
"""Tạo báo cáo Markdown và bản tóm tắt ngắn (Telegram)."""
from __future__ import annotations

import pandas as pd

from .diff import market_flow

LABEL = {"BUY": "Tăng vị thế (suy luận)", "SELL": "Giảm vị thế (suy luận)",
         "NEW_IN_TOP10": "Vào top 10", "EXIT_TOP10": "Rời top 10", "CORP_ACTION?": "Nghi do quyền",
         "PRORATA": "Đổi đồng loạt (ước tính)", "UNCONFIRMED?": "Khối lượng đổi — chưa tách được quy mô quỹ",
         "WEIGHT_CHANGE?": "Tỷ trọng đổi — chưa suy ra giao dịch"}
CAVEAT = [
    "Fmarket chỉ công bố **top 10** cổ phiếu mỗi quỹ: \"Vào/Rời top 10\" chưa chắc là mua mới/bán hết.",
    "Tăng/giảm vị thế là **suy luận** từ khối lượng sau khi dùng trung vị các mã chung làm proxy quy mô quỹ; không phải dữ liệu lệnh hay xác nhận giao dịch.",
    "Khi có dưới 4 mã chung với khối lượng hợp lệ, không thể tách đáng tin thay đổi vị thế khỏi dòng tiền vào/ra; hệ thống gắn \"Chưa xác nhận\" và loại khỏi dòng tiền ròng.",
    "Khi chỉ có tỷ trọng mà thiếu khối lượng, tỷ trọng có thể đổi do giá, dòng tiền hoặc corporate actions; hệ thống chỉ ghi \"Tỷ trọng đổi — chưa suy ra giao dịch\".",
    "\"Nghi do quyền\" là heuristic từ các quỹ cùng đổi khối lượng gần nhau (cổ tức cổ phiếu, thưởng, chia tách); chưa được xác minh bằng lịch corporate actions chính thức.",
    "Giá trị nắm giữ ước tính theo giá tại kỳ báo cáo, không phải giá mua/bán thực tế. Sidecar provenance tách effective/published/observed/ingested; legacy observed_date chỉ có độ chính xác theo ngày, còn ngày mirror commit là xấp xỉ và không PIT-safe.",
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
        return {"flow": flow, "top_buy": flow, "top_sell": flow, "unconfirmed": flow, "watch": changes.iloc[0:0]}
    top_buy = flow[flow.net_value_bn > 0].sort_values("net_value_bn", ascending=False).head(top_n)
    top_sell = flow[flow.net_value_bn < 0].sort_values("net_value_bn").head(top_n)
    no_value = flow.net_value_bn.isna() & ((flow.n_buy + flow.n_sell) > 0)
    unconfirmed = flow[(flow.n_unconfirmed > 0) | no_value]
    watch = changes[changes.ticker.isin(watchlist)] if not changes.empty else changes
    return {"flow": flow, "top_buy": top_buy, "top_sell": top_sell, "unconfirmed": unconfirmed, "watch": watch}


def _flow_table(df: pd.DataFrame) -> list[str]:
    lines = ["| Mã | Tăng vị thế* | Giảm vị thế* | Chưa xác nhận | Vào top 10 | Rời top 10 | Ròng ước tính (tỷ đồng) |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for r in df.itertuples():
        value = "—" if pd.isna(r.net_value_bn) else f"{r.net_value_bn:+.1f}"
        lines.append(f"| {r.ticker} | {r.n_buy} | {r.n_sell} | {r.n_unconfirmed} | {r.n_new} | {r.n_exit} | {value} |")
    return lines


def render_report(date: str, changes: pd.DataFrame, info: dict, cfg: dict) -> str:
    wl = cfg["watchlist"]["tickers"]
    s = summarize(changes, wl, cfg["report"]["top_n"])
    L = [f"# Thay đổi danh mục quỹ mở — {date}", ""]
    L.append(f"- Quỹ kiểm tra đầy đủ: **{info['n_ok']}/{info['n_total']}** (lỗi: {info['n_failed']}; ghi một phần: {info.get('n_partial', 0)})")
    L.append(f"- Quỹ có kỳ công bố mới: **{info['n_changed']}** | Ghi mốc lần đầu: {info['n_baseline']}")
    if info.get("n_empty_holdings", 0):
        L.append(f"- Cảnh báo chất lượng dữ liệu: **{info['n_empty_holdings']}** quỹ cổ phiếu/cân bằng trả về danh mục rỗng; không suy ra là không nắm giữ.")
    if info.get("n_nav_invalid", 0) or info.get("n_nav_history_failed", 0):
        L.append(f"- NAV bổ sung: {info.get('n_nav_invalid', 0)} giá trị NAV không hợp lệ bị bỏ qua; {info.get('n_nav_history_failed', 0)} lỗi tải lịch sử.")
    if info.get("n_quality_reports") is not None:
        L.append(f"- Validation: **{info.get('n_quality_reports', 0)}** report; {info.get('n_quality_errors', 0)} lỗi cứng; {info.get('n_quality_warnings', 0)} cảnh báo; {info.get('n_quality_rejected', 0)} dataset phụ bị từ chối.")
    if info.get("n_provenance_observations") is not None:
        L.append(f"- Provenance: **{info.get('n_provenance_observations', 0)}** observation rows appended; observed/published times không biết được giữ trống, không suy ra từ kỳ hiệu lực.")
    if info.get("n_source_events") is not None:
        L.append(f"- Source trace: **{info.get('n_source_events', 0)}** request events appended; trạng thái này không xác nhận điều khoản sử dụng hoặc độ đầy đủ của nguồn.")
    if info.get("n_provenance_write_failed", 0) or info.get("n_source_event_write_failed", 0):
        L.append("- **Lỗi vận hành:** provenance/quality/source-event sidecar ghi không đầy đủ; kiểm tra last_run.json và quyền/schema CSV.")
    if info.get("empty_tables"):
        items = ", ".join(f"{name}: {count}" for name, count in sorted(info["empty_tables"].items()))
        L.append(f"- Bảng bổ sung trả rỗng ({items}); snapshot cũ được giữ lại, không xem là dữ liệu hiện tại hoặc tỷ trọng 0.")
    if info.get("failed"):
        L.append(f"- Quỹ lỗi: {', '.join(info['failed'])}")
    L.append("")
    if changes.empty:
        L += ["Không có thay đổi vượt ngưỡng trong lần chạy này.", ""]
    else:
        if not s["top_buy"].empty:
            L += ["## Tăng vị thế ròng ước tính: lớn nhất", ""] + _flow_table(s["top_buy"]) + [""]
        if not s["top_sell"].empty:
            L += ["## Giảm vị thế ròng ước tính: lớn nhất", ""] + _flow_table(s["top_sell"]) + [""]
        if not s["unconfirmed"].empty:
            L += ["## Thay đổi chưa xác nhận / thiếu giá trị ước tính", ""] + _flow_table(s["unconfirmed"]) + [""]
        L += [f"## Watchlist ({', '.join(wl)})", ""]
        if s["watch"].empty:
            L += ["Không quỹ nào thay đổi các mã này.", ""]
        else:
            for r in s["watch"].sort_values(["ticker", "fund"]).itertuples():
                line = (f"- **{r.ticker}** | {r.fund} ({r.mgmt_co}): {LABEL.get(r.action, r.action)}"
                        f" | tỷ trọng {_pct(r.w_prev)} → {_pct(r.w_new)}"
                        + (f" | KL {_vol(r.vol_pct)}" if pd.notna(r.vol_pct) else "")
                        + (f" | ~{_bn(r.est_value_vnd)} tỷ (ước tính)" if pd.notna(r.est_value_vnd) else ""))
                if pd.notna(r.flag) and r.flag:
                    line += f" | cờ: `{r.flag}`"
                L.append(line)
            L.append("")
        L += ["## Chi tiết theo quỹ", ""]
        for (fund, co), g in changes.groupby(["fund", "mgmt_co"], dropna=False):
            L += [f"### {fund} ({co})", "", "| Mã | Hành động | Cờ / căn cứ | Tỷ trọng | Khối lượng | Giá trị ước tính (tỷ) |",
                  "|---|---|---|---|---|---|"]
            g = g.assign(_k=g.est_value_vnd.abs().fillna(-1)).sort_values("_k", ascending=False)
            for r in g.itertuples():
                basis_flag = " / ".join(str(x) for x in (r.basis, r.flag) if pd.notna(x) and x)
                value = _bn(r.est_value_vnd) if pd.notna(r.est_value_vnd) else "—"
                L.append(f"| {r.ticker} | {LABEL.get(r.action, r.action)} | {basis_flag} | {_pct(r.w_prev)} → {_pct(r.w_new)} "
                         f"| {_vol(r.vol_pct)} | {value} |")
            L.append("")
    L += ["## Lưu ý", ""] + [f"- {c}" for c in CAVEAT]
    return "\n".join(L) + "\n"


def render_text(date: str, changes: pd.DataFrame, info: dict, cfg: dict) -> str:
    s = summarize(changes, cfg["watchlist"]["tickers"], 5)
    t = [f"Quỹ mở {date}: {info['n_changed']} quỹ có kỳ công bố mới ({info['n_ok']}/{info['n_total']} quỹ OK)"]
    if changes.empty:
        return t[0] + "\nKhông có thay đổi vượt ngưỡng."
    if not s["top_buy"].empty:
        t.append("Tăng vị thế ước tính: " + ", ".join(f"{r.ticker} {r.net_value_bn:+.1f}tỷ" for r in s["top_buy"].itertuples()))
    if not s["top_sell"].empty:
        t.append("Giảm vị thế ước tính: " + ", ".join(f"{r.ticker} {r.net_value_bn:+.1f}tỷ" for r in s["top_sell"].itertuples()))
    for r in s["watch"].sort_values(["ticker", "fund"]).itertuples():
        t.append(f"* {r.ticker} {r.fund}: {LABEL.get(r.action, r.action)} {_pct(r.w_prev)}→{_pct(r.w_new)}")
    return "\n".join(t)
