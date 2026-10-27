# -*- coding: utf-8 -*-
"""Phát hiện quỹ MUA/BÁN gì giữa hai kỳ công bố.

Nguyên tắc:
- Tỷ trọng đổi một phần do giá cổ phiếu biến động, nên ƯU TIÊN so sánh KHỐI LƯỢNG cổ phiếu nắm giữ.
- Fmarket chỉ có top 10: mã mới xuất hiện / rời top 10 là dữ liệu bị kiểm duyệt (censored),
  không chắc là mua mới hay bán hết.
- Quỹ nhận/chi trả vốn sẽ nới/thu hẹp MỌI vị thế cùng tỷ lệ (không phải quan điểm đầu tư): ước lượng hệ số quy mô
  của quỹ bằng trung vị thay đổi khối lượng các mã chung (cần >= 4 mã); phần chênh so với hệ số đó mới là MUA/BÁN
  chủ động. Phần thay đổi đồng loạt được gắn nhãn PRORATA.
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


def _price_vnd(r) -> float:
    """Giá ngầm định tại kỳ báo cáo = giá trị tài sản / khối lượng; thiếu thì dùng giá thị trường."""
    av, vol, pk = _get(r, "asset_value"), _get(r, "volume"), _get(r, "price_k")
    if pd.notna(av) and pd.notna(vol) and vol > 0:
        return float(av) / float(vol)
    return float(pk) * 1000 if pd.notna(pk) else np.nan


def diff_holdings(prev: pd.DataFrame, new: pd.DataFrame, min_vol_pct: float = 0.05,
                  min_w_delta: float = 0.005) -> pd.DataFrame:
    p = prev.drop_duplicates("ticker").set_index("ticker")
    n = new.drop_duplicates("ticker").set_index("ticker")
    both = [t for t in n.index if t in p.index]
    pcts = {}
    for t in both:
        vp, vn = _get(p.loc[t], "volume"), _get(n.loc[t], "volume")
        if pd.notna(vp) and pd.notna(vn) and vp > 0:
            pcts[t] = vn / vp - 1.0
    scale = float(np.median(list(pcts.values()))) if len(pcts) >= 4 else 0.0

    rows = []
    for t in sorted(set(p.index) | set(n.index)):
        rp = p.loc[t] if t in p.index else None
        rn = n.loc[t] if t in n.index else None
        wp, wn = _get(rp, "weight"), _get(rn, "weight")
        vp, vn = _get(rp, "volume"), _get(rn, "volume")
        rec = dict(ticker=t, w_prev=wp, w_new=wn, vol_prev=vp, vol_new=vn, vol_pct=np.nan, vol_rel=np.nan,
                   vol_scale=np.nan, est_value_vnd=np.nan, censored=False, flag="")
        rec["w_delta"] = (0 if pd.isna(wn) else wn) - (0 if pd.isna(wp) else wp)
        if rp is None:
            rec.update(action="NEW_IN_TOP10", basis="rank", censored=True)
        elif rn is None:
            rec.update(action="EXIT_TOP10", basis="rank", censored=True)
        elif t in pcts:
            pct = pcts[t]
            rel = (1.0 + pct) / (1.0 + scale) - 1.0
            px = _price_vnd(rn)
            rec.update(basis="volume", vol_pct=pct, vol_rel=rel, vol_scale=scale)
            if abs(rel) >= min_vol_pct:       # giao dịch chủ động so với quy mô quỹ
                active = vn - vp * (1.0 + scale)
                rec.update(action="BUY" if rel > 0 else "SELL",
                           est_value_vnd=active * px if pd.notna(px) else np.nan)
            elif abs(pct) >= min_vol_pct:     # đổi đồng loạt theo quy mô quỹ
                rec.update(action="PRORATA", flag="PRORATA",
                           est_value_vnd=(vn - vp) * px if pd.notna(px) else np.nan)
            else:
                continue
        else:
            if abs(rec["w_delta"]) < min_w_delta:
                continue
            rec.update(action="BUY" if rec["w_delta"] > 0 else "SELL", basis="weight")
        rows.append(rec)
    return pd.DataFrame(rows)


def flag_corporate_actions(cur: pd.DataFrame, history: pd.DataFrame | None = None, min_funds: int = 3,
                           spread: float = 0.01, min_pct: float = 0.03) -> pd.DataFrame:
    """Gắn cờ các thay đổi khối lượng nhiều quỹ giống nhau (nghi do quyền, không phải giao dịch).

    `history` (các thay đổi vài ngày gần đây) giúp phát hiện khi các quỹ công bố lệch ngày nhau.
    Chỉ sửa các dòng của `cur`.
    """
    cur = cur.copy().reset_index(drop=True)
    if cur.empty:
        return cur
    cur["_cur"] = True
    pool = cur
    if history is not None and not history.empty:
        h = history.copy()
        h["_cur"] = False
        pool = pd.concat([cur, h], ignore_index=True)
    m = pool.action.isin(["BUY", "SELL", "CORP_ACTION?"]) & (pool.basis == "volume") & pool.vol_pct.notna()
    for t, g in pool[m].groupby("ticker"):
        g = g.drop_duplicates(["fund"], keep="first")
        med = g.vol_pct.median()
        close = g[(g.vol_pct - med).abs() <= spread]
        if len(close) >= min_funds and abs(med) >= min_pct:
            idx = cur.index[(cur.ticker == t) & (cur.fund.isin(close.fund)) & cur.action.isin(["BUY", "SELL"])
                            & (cur.basis == "volume")]
            cur.loc[idx, "flag"] = "CORP_ACTION?"
            cur.loc[idx, "action"] = "CORP_ACTION?"
            cur.loc[idx, "est_value_vnd"] = np.nan
    return cur.drop(columns="_cur")


def market_flow(changes: pd.DataFrame) -> pd.DataFrame:
    """Tổng hợp theo mã: số quỹ mua/bán, vào/ra top 10, giá trị ròng ước tính (tỷ đồng, chỉ từ khối lượng)."""
    if changes is None or changes.empty:
        return pd.DataFrame(columns=["ticker", "n_buy", "n_sell", "n_new", "n_exit", "net_value_bn"])
    c = changes
    out = c.groupby("ticker").agg(
        n_buy=("action", lambda s: (s == "BUY").sum()),
        n_sell=("action", lambda s: (s == "SELL").sum()),
        n_new=("action", lambda s: (s == "NEW_IN_TOP10").sum()),
        n_exit=("action", lambda s: (s == "EXIT_TOP10").sum()),
    )
    v = c[c.action.isin(["BUY", "SELL"]) & (c.basis == "volume")].groupby("ticker").est_value_vnd.sum() / 1e9
    out["net_value_bn"] = v
    out["net_value_bn"] = out.net_value_bn.fillna(0.0)
    return out.reset_index()
