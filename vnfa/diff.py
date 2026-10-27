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


def _num_token(value) -> str:
    if not _finite_number(value):
        return "NA"
    return f"{round(float(value), 8):.8f}"


def _prepare_snapshot(df: pd.DataFrame | None) -> tuple[pd.DataFrame, set[str]]:
    """Canonicalize snapshot deterministically so row permutations never alter diffs.

    Exact duplicate rows are collapsed cleanly; conflicting duplicate tickers are
    recorded as ambiguous so they cannot pollute the median fund-scale proxy or emit
    a false BUY/SELL inference.
    """
    if df is None or df.empty or "ticker" not in df.columns:
        return pd.DataFrame().set_index(pd.Index([], name="ticker")), set()
    clean = df.copy()
    clean["ticker"] = clean["ticker"].astype("string").str.strip().str.upper()
    clean = clean[clean["ticker"].notna() & clean["ticker"].ne("")].copy()
    if clean.empty:
        return pd.DataFrame().set_index(pd.Index([], name="ticker")), set()

    for num_col in ("weight", "volume", "price_k", "asset_value"):
        clean[f"_sort_{num_col}"] = pd.to_numeric(clean[num_col], errors="coerce") if num_col in clean.columns else np.nan
    clean = clean.sort_values(
        ["ticker", "_sort_weight", "_sort_volume", "_sort_asset_value", "_sort_price_k"],
        ascending=[True, False, False, False, False],
        na_position="last",
        kind="stable",
    )

    ambiguous: set[str] = set()
    dup_mask = clean["ticker"].duplicated(keep=False)
    if dup_mask.any():
        for ticker, group in clean[dup_mask].groupby("ticker", sort=False):
            signatures = {
                (_num_token(row.get("weight")), _num_token(row.get("volume")))
                for _, row in group.iterrows()
            }
            if len(signatures) > 1:
                ambiguous.add(str(ticker))

    sort_cols = [c for c in clean.columns if c.startswith("_sort_")]
    indexed = clean.drop(columns=sort_cols).drop_duplicates("ticker", keep="first").set_index("ticker")
    indexed.index = indexed.index.astype(str)
    return indexed, ambiguous


def diff_holdings(prev: pd.DataFrame, new: pd.DataFrame, min_vol_pct: float = 0.05,
                  min_w_delta: float = 0.005) -> pd.DataFrame:
    """So sánh hai snapshot top-10, giữ riêng quan sát và suy luận.

    Chỉ ước lượng phần tăng/giảm chủ động khi có ít nhất bốn mã chung có khối lượng
    dương hợp lệ để làm proxy quy mô quỹ. Ít hơn bốn mã thì thay đổi khối lượng không đủ
    phân biệt với dòng tiền vào/ra; thay đổi tỷ trọng khi thiếu khối lượng cũng không
    đủ phân biệt với biến động giá. Hai trường hợp đó được đánh dấu chưa xác nhận.
    """
    p, amb_prev = _prepare_snapshot(prev)
    n, amb_new = _prepare_snapshot(new)
    ambiguous = amb_prev | amb_new
    if p.empty and n.empty:
        return pd.DataFrame(columns=[c for c in CHANGE_COLUMNS if c not in {"observed_date", "fund", "mgmt_co", "asset_class"}])

    both = [t for t in n.index if t in p.index and t not in ambiguous]
    pcts = {}
    for t in both:
        vp, vn = _get(p.loc[t], "volume"), _get(n.loc[t], "volume")
        # Một vị thế đang nằm trong top-N không thể có khối lượng <= 0 hợp lệ;
        # giá trị 0 hoặc âm là dữ liệu thiếu/lỗi, không được tính thành bán 100%.
        if _finite_number(vp) and _finite_number(vn) and float(vp) > 0 and float(vn) > 0:
            pcts[t] = float(vn) / float(vp) - 1.0
    scale_known = len(pcts) >= 4
    scale = float(np.median(list(pcts.values()))) if scale_known else np.nan
    if scale_known and (not np.isfinite(scale) or 1.0 + scale <= 0):
        scale_known, scale = False, np.nan

    rows = []
    for t in sorted(set(p.index) | set(n.index)):
        rp = p.loc[t] if t in p.index else None
        rn = n.loc[t] if t in n.index else None
        wp = _get(rp, "weight")
        wn = _get(rn, "weight")
        vp = _get(rp, "volume")
        vn = _get(rn, "volume")
        wp_val = float(wp) if _finite_number(wp) else np.nan
        wn_val = float(wn) if _finite_number(wn) else np.nan
        vp_val = float(vp) if _finite_number(vp) else np.nan
        vn_val = float(vn) if _finite_number(vn) else np.nan

        rec = dict(ticker=t, w_prev=wp_val, w_new=wn_val, vol_prev=vp_val, vol_new=vn_val,
                   vol_pct=np.nan, vol_rel=np.nan, vol_scale=np.nan, est_value_vnd=np.nan,
                   censored=False, flag="")
        if np.isfinite(wp_val) and np.isfinite(wn_val):
            rec["w_delta"] = wn_val - wp_val
        elif rp is None and np.isfinite(wn_val):
            rec["w_delta"] = wn_val
        elif rn is None and np.isfinite(wp_val):
            rec["w_delta"] = -wp_val
        else:
            rec["w_delta"] = np.nan

        if t in ambiguous:
            rec.update(action="UNCONFIRMED?", basis="duplicate", flag="DUPLICATE_TICKER_AMBIGUOUS",
                       censored=(rp is None or rn is None))
            rows.append(rec)
            continue

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
                    active = vn_val - vp_val * (1.0 + scale)
                    rec.update(action="BUY" if rel > 0 else "SELL", flag="FUND_SCALE_MEDIAN_PROXY",
                               est_value_vnd=active * px if np.isfinite(px) else np.nan)
                elif abs(pct) >= min_vol_pct:     # thay đổi gần đồng loạt theo proxy quy mô quỹ
                    gross = (vn_val - vp_val) * px if np.isfinite(px) else np.nan
                    rec.update(action="PRORATA", flag="PRORATA", est_value_vnd=gross)
                else:
                    continue
        elif (np.isfinite(vp_val) and vp_val <= 0) or (np.isfinite(vn_val) and vn_val <= 0):
            # Khối lượng <= 0 trên dòng đang hiện diện không đáng tin để suy ra giao dịch chủ động.
            rec["vol_prev"] = vp_val if vp_val > 0 else np.nan
            rec["vol_new"] = vn_val if vn_val > 0 else np.nan
            rec.update(action="UNCONFIRMED?", basis="volume", flag="NONPOSITIVE_VOLUME_UNCONFIRMED")
        else:
            # Tỷ trọng đơn lẻ có thể đổi do giá, dòng tiền hoặc corporate actions; không gán BUY/SELL.
            if not (np.isfinite(wp_val) and np.isfinite(wn_val)) or abs(rec["w_delta"]) < min_w_delta:
                continue
            rec.update(action="WEIGHT_CHANGE?", basis="weight", flag="WEIGHT_ONLY_UNCONFIRMED")
        rows.append(rec)
    if not rows:
        return pd.DataFrame(columns=["ticker", "w_prev", "w_new", "vol_prev", "vol_new", "vol_pct",
                                     "vol_rel", "vol_scale", "est_value_vnd", "censored", "flag",
                                     "w_delta", "action", "basis"])
    return pd.DataFrame(rows)


def flag_corporate_actions(cur: pd.DataFrame, history: pd.DataFrame | None = None, min_funds: int = 3,
                           spread: float = 0.01, min_pct: float = 0.03,
                           update_history: bool = False) -> pd.DataFrame | tuple[pd.DataFrame, pd.DataFrame]:
    """Gắn cờ thay đổi khối lượng nhiều quỹ giống nhau (nghi do quyền, không phải xác nhận).

    `history` giúp phát hiện quỹ công bố lệch ngày. Mặc định chỉ trả các dòng hiện tại để
    giữ tương thích; `update_history=True` trả thêm các dòng lịch sử bị gắn cờ để caller
    có thể cập nhật journal và không để tín hiệu cũ tiếp tục bị hiểu là active BUY/SELL.
    """
    cur = cur.copy().reset_index(drop=True)
    hist = history.copy().reset_index(drop=True) if history is not None else pd.DataFrame()
    history_updates: set[int] = set()
    if cur.empty:
        return (cur, hist.iloc[0:0].copy()) if update_history else cur
    cur["_cur"] = True
    cur["_date_sort"] = cur["observed_date"].astype(str) if "observed_date" in cur.columns else ""
    if not hist.empty:
        hist_tagged = hist.assign(
            _cur=False,
            _date_sort=hist["observed_date"].astype(str) if "observed_date" in hist.columns else "",
        )
        pool = pd.concat([cur, hist_tagged], ignore_index=True)
    else:
        pool = cur.copy()
    not_bond = pool["asset_class"].fillna("STOCK").ne("BOND") if "asset_class" in pool.columns else True
    m = not_bond & pool.action.isin(["BUY", "SELL", "CORP_ACTION?"]) & (pool.basis == "volume") & pool.vol_pct.notna()
    for t, g in pool[m].groupby("ticker", sort=True):
        # Deterministic ordering so row permutations in cur/history never change median or flagging.
        g_sorted = g.sort_values(["_cur", "_date_sort", "fund", "vol_pct"],
                                 ascending=[False, False, True, True], kind="stable")
        g_unique = g_sorted.drop_duplicates(["fund"], keep="first")
        med = float(g_unique.vol_pct.median())
        close = g_unique[(g_unique.vol_pct - med).abs() <= spread]
        if len(close) >= min_funds and abs(med) >= min_pct:
            cur_not_bond = cur["asset_class"].fillna("STOCK").ne("BOND") if "asset_class" in cur.columns else True
            cur_idx = cur.index[cur_not_bond & (cur.ticker == t) & (cur.fund.isin(close.fund))
                                & cur.action.isin(["BUY", "SELL"]) & (cur.basis == "volume")
                                & ((cur.vol_pct - med).abs() <= spread)]
            cur.loc[cur_idx, "flag"] = "CORP_ACTION?"
            cur.loc[cur_idx, "action"] = "CORP_ACTION?"
            cur.loc[cur_idx, "est_value_vnd"] = np.nan
            if update_history and not hist.empty:
                hist_not_bond = hist["asset_class"].fillna("STOCK").ne("BOND") if "asset_class" in hist.columns else True
                history_idx = hist.index[hist_not_bond & (hist.ticker == t) & (hist.fund.isin(close.fund))
                                         & hist.action.isin(["BUY", "SELL"])
                                         & (hist.basis == "volume")
                                         & ((hist.vol_pct - med).abs() <= spread)]
                hist.loc[history_idx, "flag"] = "CORP_ACTION?"
                hist.loc[history_idx, "action"] = "CORP_ACTION?"
                hist.loc[history_idx, "est_value_vnd"] = np.nan
                history_updates.update(int(i) for i in history_idx)
    cur = cur.drop(columns=["_cur", "_date_sort"], errors="ignore")
    if update_history:
        updated = hist.loc[sorted(history_updates)].copy() if history_updates else hist.iloc[0:0].copy()
        return cur, updated
    return cur


def market_flow(changes: pd.DataFrame) -> pd.DataFrame:
    """Tổng hợp theo mã; giá trị ròng ước tính chỉ tính từ thay đổi khối lượng đủ điều kiện.

    Giá trị thiếu được giữ là NA (không thay bằng 0). BUY/SELL lịch sử dựa trên tỷ trọng,
    PRORATA và CORP_ACTION? đều không được tính là dòng tiền chủ động.
    """
    columns = ["ticker", "n_buy", "n_sell", "n_unconfirmed", "n_new", "n_exit", "net_value_bn"]
    if changes is None or changes.empty:
        return pd.DataFrame(columns=columns)
    c = changes.copy()
    action = c["action"]
    basis = c["basis"] if "basis" in c else pd.Series("", index=c.index)
    active_buy = action.eq("BUY") & basis.eq("volume")
    active_sell = action.eq("SELL") & basis.eq("volume")
    uncertain = action.isin(["UNCONFIRMED?", "WEIGHT_CHANGE?"]) | (basis.ne("volume") & action.isin(["BUY", "SELL"]))
    c["_buy"] = active_buy
    c["_sell"] = active_sell
    c["_uncertain"] = uncertain
    out = c.groupby("ticker", sort=True).agg(
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
