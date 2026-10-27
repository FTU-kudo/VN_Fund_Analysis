# -*- coding: utf-8 -*-
"""Ghi nhận thay đổi top holdings và suy luận thay đổi vị thế giữa hai kỳ công bố.

Các nhãn BUY/SELL là suy luận dựa trên proxy quy mô quỹ, không phải nhật ký lệnh.
Nguyên tắc:
- Tỷ trọng đổi một phần do giá cổ phiếu biến động, nên ƯU TIÊN so sánh KHỐI LƯỢNG cổ phiếu nắm giữ.
- Fmarket chỉ có top 10: mã mới xuất hiện / rời top 10 là dữ liệu bị kiểm duyệt (censored),
  không chắc là mua mới hay bán hết.
- Quỹ nhận/chi trả vốn sẽ nới/thu hẹp MỌI vị thế cùng tỷ lệ (không phải quan điểm đầu tư): ước lượng hệ số quy mô
  của quỹ bằng trung vị thay đổi khối lượng các mã chung (cần >= 4 mã); phần chênh so với hệ số đó là suy luận
  tăng/giảm vị thế, không phải giao dịch xác nhận. Phần thay đổi đồng loạt được gắn nhãn PRORATA.
- Quyền (cổ tức cổ phiếu, thưởng, chia tách) làm khối lượng đổi mà quỹ không giao dịch:
  nhiều quỹ cùng đổi một mã với tỷ lệ gần như nhau => gắn cờ CORP_ACTION?.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CHANGE_COLUMNS = ["observed_date", "fund", "mgmt_co", "ticker", "action", "basis",
                  "w_prev", "w_new", "w_delta", "vol_prev", "vol_new", "vol_pct", "vol_rel",
                  "vol_scale", "est_value_vnd", "censored", "flag", "asset_class"]


def _get(r, col):
    if r is None:
        return np.nan
    v = r.get(col, np.nan)
    return np.nan if v is None else v


def _finite_number(x) -> bool:
    try:
        return pd.notna(x) and np.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _price_vnd(r) -> float:
    """Giá ngầm định tại kỳ báo cáo = giá trị tài sản / khối lượng; thiếu thì dùng giá thị trường."""
    av, vol, pk = _get(r, "asset_value"), _get(r, "volume"), _get(r, "price_k")
    if _finite_number(av) and _finite_number(vol) and float(vol) > 0:
        value = float(av) / float(vol)
        if np.isfinite(value) and value > 0:
            return value
    if _finite_number(pk) and float(pk) > 0:
        return float(pk) * 1000
    return np.nan


def diff_holdings(prev: pd.DataFrame, new: pd.DataFrame, min_vol_pct: float = 0.05,
                  min_w_delta: float = 0.005) -> pd.DataFrame:
    """So sánh hai snapshot top-10, giữ riêng quan sát và suy luận.

    Chỉ ước lượng phần tăng/giảm chủ động khi có ít nhất bốn mã chung có khối lượng
    hợp lệ để làm proxy quy mô quỹ. Ít hơn bốn mã thì thay đổi khối lượng không đủ
    phân biệt với dòng tiền vào/ra; thay đổi tỷ trọng khi thiếu khối lượng cũng không
    đủ phân biệt với biến động giá. Hai trường hợp đó được đánh dấu chưa xác nhận.
    """
    p = prev.drop_duplicates("ticker").set_index("ticker")
    n = new.drop_duplicates("ticker").set_index("ticker")
    both = [t for t in n.index if t in p.index]
    pcts = {}
    for t in both:
        vp, vn = _get(p.loc[t], "volume"), _get(n.loc[t], "volume")
        if _finite_number(vp) and _finite_number(vn) and float(vp) > 0 and float(vn) >= 0:
            pcts[t] = float(vn) / float(vp) - 1.0
    scale_known = len(pcts) >= 4
    scale = float(np.median(list(pcts.values()))) if scale_known else np.nan
    if scale_known and (not np.isfinite(scale) or 1.0 + scale <= 0):
        scale_known, scale = False, np.nan

    rows = []
    for t in sorted(set(p.index) | set(n.index)):
        rp = p.loc[t] if t in p.index else None
        rn = n.loc[t] if t in n.index else None
        wp, wn = _get(rp, "weight"), _get(rn, "weight")
        vp, vn = _get(rp, "volume"), _get(rn, "volume")
        rec = dict(ticker=t, w_prev=wp, w_new=wn, vol_prev=vp, vol_new=vn, vol_pct=np.nan, vol_rel=np.nan,
                   vol_scale=np.nan, est_value_vnd=np.nan, censored=False, flag="")
        if _finite_number(wp) and _finite_number(wn):
            rec["w_delta"] = float(wn) - float(wp)
        elif rp is None and _finite_number(wn):
            rec["w_delta"] = float(wn)
        elif rn is None and _finite_number(wp):
            rec["w_delta"] = -float(wp)
        else:
            rec["w_delta"] = np.nan

        if rp is None:
            rec.update(action="NEW_IN_TOP10", basis="rank", censored=True)
        elif rn is None:
            rec.update(action="EXIT_TOP10", basis="rank", censored=True)
        elif t in pcts:
            pct = pcts[t]
            rec.update(basis="volume", vol_pct=pct)
            if not scale_known:
                if abs(pct) < min_vol_pct:
                    continue
                rec.update(action="UNCONFIRMED?", flag="FUND_SCALE_UNIDENTIFIED")
            else:
                rel = (1.0 + pct) / (1.0 + scale) - 1.0
                px = _price_vnd(rn)
                rec.update(vol_rel=rel, vol_scale=scale)
                if abs(rel) >= min_vol_pct:       # suy luận từ proxy quy mô quỹ, không phải blotter giao dịch
                    active = float(vn) - float(vp) * (1.0 + scale)
                    rec.update(action="BUY" if rel > 0 else "SELL", flag="FUND_SCALE_MEDIAN_PROXY",
                               est_value_vnd=active * px if np.isfinite(px) else np.nan)
                elif abs(pct) >= min_vol_pct:     # thay đổi gần đồng loạt theo proxy quy mô quỹ
                    gross = (float(vn) - float(vp)) * px if np.isfinite(px) else np.nan
                    rec.update(action="PRORATA", flag="PRORATA", est_value_vnd=gross)
                else:
                    continue
        else:
            # Tỷ trọng đơn lẻ có thể đổi do giá, dòng tiền hoặc corporate actions; không gán BUY/SELL.
            if not (_finite_number(wp) and _finite_number(wn)) or abs(rec["w_delta"]) < min_w_delta:
                continue
            rec.update(action="WEIGHT_CHANGE?", basis="weight", flag="WEIGHT_ONLY_UNCONFIRMED")
        rows.append(rec)
    return pd.DataFrame(rows)


def flag_corporate_actions(cur: pd.DataFrame, history: pd.DataFrame | None = None, min_funds: int = 3,
                           spread: float = 0.01, min_pct: float = 0.03,
                           update_history: bool = False) -> pd.DataFrame | tuple[pd.DataFrame, pd.DataFrame]:
    """Gắn cờ thay đổi khối lượng nhiều quỹ giống nhau (nghi do quyền, không phải xác nhận).

    `history` giúp phát hiện quỹ công bố lệch ngày. Mặc định chỉ trả các dòng hiện tại để
    giữ tương thích; `update_history=True` trả thêm các dòng lịch sử bị gắn cờ để caller
    có thể cập nhật journal và không để tín hiệu cũ tiếp tục bị hiểu là BUY/SELL.
    """
    cur = cur.copy().reset_index(drop=True)
    hist = history.copy().reset_index(drop=True) if history is not None else pd.DataFrame()
    history_updates: set[int] = set()
    if cur.empty:
        return (cur, hist.iloc[0:0].copy()) if update_history else cur
    cur["_cur"] = True
    pool = pd.concat([cur, hist.assign(_cur=False)], ignore_index=True) if not hist.empty else cur.copy()
    m = pool.action.isin(["BUY", "SELL", "CORP_ACTION?"]) & (pool.basis == "volume") & pool.vol_pct.notna()
    for t, g in pool[m].groupby("ticker"):
        g = g.drop_duplicates(["fund"], keep="first")
        med = g.vol_pct.median()
        close = g[(g.vol_pct - med).abs() <= spread]
        if len(close) >= min_funds and abs(med) >= min_pct:
            cur_idx = cur.index[(cur.ticker == t) & (cur.fund.isin(close.fund)) & cur.action.isin(["BUY", "SELL"])
                                & (cur.basis == "volume")]
            cur.loc[cur_idx, "flag"] = "CORP_ACTION?"
            cur.loc[cur_idx, "action"] = "CORP_ACTION?"
            cur.loc[cur_idx, "est_value_vnd"] = np.nan
            if update_history and not hist.empty:
                history_idx = hist.index[(hist.ticker == t) & (hist.fund.isin(close.fund))
                                         & hist.action.isin(["BUY", "SELL"])
                                         & (hist.basis == "volume")
                                         & ((hist.vol_pct - med).abs() <= spread)]
                hist.loc[history_idx, "flag"] = "CORP_ACTION?"
                hist.loc[history_idx, "action"] = "CORP_ACTION?"
                hist.loc[history_idx, "est_value_vnd"] = np.nan
                history_updates.update(int(i) for i in history_idx)
    cur = cur.drop(columns="_cur")
    if update_history:
        updated = hist.loc[sorted(history_updates)].copy() if history_updates else hist.iloc[0:0].copy()
        return cur, updated
    return cur


def market_flow(changes: pd.DataFrame) -> pd.DataFrame:
    """Tổng hợp theo mã; giá trị ròng ước tính chỉ tính từ thay đổi khối lượng đủ điều kiện.

    Giá trị thiếu được giữ là NA (không thay bằng 0). BUY/SELL lịch sử dựa trên tỷ trọng
    cũng không được tính là giao dịch chủ động.
    """
    columns = ["ticker", "n_buy", "n_sell", "n_unconfirmed", "n_new", "n_exit", "net_value_bn"]
    if changes is None or changes.empty:
        return pd.DataFrame(columns=columns)
    c = changes.copy()
    action = c["action"]
    basis = c["basis"] if "basis" in c else pd.Series("", index=c.index)
    active_buy = action.eq("BUY") & basis.eq("volume")
    active_sell = action.eq("SELL") & basis.eq("volume")
    uncertain = action.isin(["UNCONFIRMED?", "WEIGHT_CHANGE?"]) | (basis.eq("weight") & action.isin(["BUY", "SELL"]))
    c["_buy"] = active_buy
    c["_sell"] = active_sell
    c["_uncertain"] = uncertain
    out = c.groupby("ticker").agg(
        n_buy=("_buy", "sum"),
        n_sell=("_sell", "sum"),
        n_unconfirmed=("_uncertain", "sum"),
        n_new=("action", lambda s: (s == "NEW_IN_TOP10").sum()),
        n_exit=("action", lambda s: (s == "EXIT_TOP10").sum()),
    )
    value_rows = c[(active_buy | active_sell)]
    # min_count=1: một nhóm chỉ có giá trị NA phải còn là NA, không được thành dòng tiền 0.
    v = value_rows.groupby("ticker").est_value_vnd.sum(min_count=1) / 1e9
    out["net_value_bn"] = v
    return out.reset_index()[columns]
